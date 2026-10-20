package app.selects.mobile

import android.Manifest
import android.app.Activity
import android.content.ContentUris
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.provider.OpenableColumns
import androidx.activity.result.contract.ActivityResultContract
import androidx.core.content.ContextCompat
import androidx.exifinterface.media.ExifInterface
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.format.ResolverStyle

object MediaAccess {
    fun isTrashed(context: Context, uri: Uri): Boolean {
        if (Build.VERSION.SDK_INT < 30) return false
        return try {
            val args =
                Bundle().apply {
                    putInt(MediaStore.QUERY_ARG_MATCH_TRASHED, MediaStore.MATCH_INCLUDE)
                }
            context.contentResolver
                .query(uri, arrayOf(MediaStore.Images.Media.IS_TRASHED), args, null)
                ?.use { it.moveToFirst() && it.getInt(0) == 1 } ?: false
        } catch (_: Exception) {
            false
        }
    }

    fun permissions(): Array<String> =
        when {
            Build.VERSION.SDK_INT >= 34 ->
                arrayOf(
                    Manifest.permission.READ_MEDIA_IMAGES,
                    Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED,
                )
            Build.VERSION.SDK_INT >= 33 -> arrayOf(Manifest.permission.READ_MEDIA_IMAGES)
            else -> arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE)
        }

    fun full(context: Context): Boolean =
        ContextCompat.checkSelfPermission(
            context,
            if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_IMAGES
            else Manifest.permission.READ_EXTERNAL_STORAGE,
        ) == PackageManager.PERMISSION_GRANTED

    fun any(context: Context): Boolean =
        full(context) ||
            (Build.VERSION.SDK_INT >= 34 &&
                ContextCompat.checkSelfPermission(
                    context,
                    Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED,
                ) == PackageManager.PERMISSION_GRANTED)

    fun location(context: Context): Boolean =
        Build.VERSION.SDK_INT < 29 ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_MEDIA_LOCATION) ==
                PackageManager.PERMISSION_GRANTED

    fun direct(uri: Uri): Boolean =
        uri.authority == MediaStore.AUTHORITY &&
            uri.pathSegments.let {
                it.size >= 4 &&
                    it[it.size - 3] == "images" &&
                    it[it.size - 2] == "media" &&
                    it.last().toLongOrNull() != null
            }

    fun images(context: Context): List<MediaEntry> {
        val collection =
            if (Build.VERSION.SDK_INT >= 29)
                MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL)
            else MediaStore.Images.Media.EXTERNAL_CONTENT_URI
        val columns =
            arrayOf(
                MediaStore.Images.Media._ID,
                MediaStore.Images.Media.DISPLAY_NAME,
                MediaStore.Images.Media.DATE_TAKEN,
                MediaStore.Images.Media.DATE_MODIFIED,
                MediaStore.Images.Media.SIZE,
                MediaStore.Images.Media.BUCKET_ID,
                MediaStore.Images.Media.BUCKET_DISPLAY_NAME,
            )
        return context.contentResolver
            .query(collection, columns, null, null, "${MediaStore.Images.Media._ID} ASC")
            ?.use { c ->
                buildList {
                    while (c.moveToNext()) {
                        val taken = c.getLong(2).takeIf { it > 0 } ?: 0
                        add(
                            MediaEntry(
                                ContentUris.withAppendedId(collection, c.getLong(0)).toString(),
                                c.getString(1) ?: "Photo",
                                taken,
                                c.getString(6) ?: "Gallery",
                                c.getString(5) ?: "",
                                c.getLong(4),
                                c.getLong(3),
                            )
                        )
                    }
                }
            } ?: throw java.io.IOException("Gallery provider unavailable")
    }

    fun selected(context: Context, uri: Uri): MediaEntry {
        if (direct(uri)) {
            val cols =
                arrayOf(
                    MediaStore.Images.Media.DISPLAY_NAME,
                    MediaStore.Images.Media.DATE_TAKEN,
                    MediaStore.Images.Media.DATE_MODIFIED,
                    MediaStore.Images.Media.SIZE,
                    MediaStore.Images.Media.BUCKET_ID,
                    MediaStore.Images.Media.BUCKET_DISPLAY_NAME,
                )
            context.contentResolver.query(uri, cols, null, null, null)?.use { c ->
                if (c.moveToFirst())
                    return MediaEntry(
                        uri.toString(),
                        c.getString(0) ?: "Photo",
                        c.getLong(1).takeIf { it > 0 } ?: 0,
                        c.getString(5) ?: "Gallery",
                        c.getString(4) ?: "",
                        c.getLong(3),
                        c.getLong(2),
                    )
            }
        }
        return context.contentResolver
            .query(
                uri,
                arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
                null,
                null,
                null,
            )
            ?.use {
                if (it.moveToFirst())
                    MediaEntry(
                        uri.toString(),
                        it.getString(0) ?: "Photo",
                        bytes = it.getLong(1),
                        album = "Selected photos",
                    )
                else null
            } ?: MediaEntry(uri.toString(), "Photo", album = "Selected photos")
    }

    fun metadata(
        context: Context,
        entry: MediaEntry,
        old: Photo?,
        locationAllowed: Boolean = location(context),
    ): MediaEntry {
        val version = if (locationAllowed) 2 else 1
        if (
            old != null &&
                old.bytes == entry.bytes &&
                old.modified == entry.modified &&
                old.metadataVersion >= version
        )
            return entry.copy(
                taken = entry.taken.takeIf { it > 0 } ?: old.taken,
                latitude = old.latitude,
                longitude = old.longitude,
                metadataRead = true,
            )
        if (!locationAllowed && entry.taken > 0) return entry.copy(metadataRead = true)
        return try {
            val uri = Uri.parse(entry.uri)
            val original =
                if (Build.VERSION.SDK_INT >= 29 && locationAllowed && direct(uri))
                    MediaStore.setRequireOriginal(uri)
                else uri
            fun read(exif: ExifInterface): MediaEntry {
                val gps = if (locationAllowed) exif.latLong else null
                return entry.copy(
                    taken =
                        entry.taken.takeIf { it > 0 }
                            ?: exifTime(
                                exif.getAttribute(ExifInterface.TAG_DATETIME_ORIGINAL),
                                exif.getAttribute(ExifInterface.TAG_OFFSET_TIME_ORIGINAL),
                            )
                            ?: entry.modified * 1000,
                    latitude = gps?.get(0),
                    longitude = gps?.get(1),
                    metadataRead = true,
                )
            }
            val result =
                context.contentResolver.openInputStream(original)?.use { read(ExifInterface(it)) }
            result ?: entry.copy(taken = entry.taken.takeIf { it > 0 } ?: entry.modified * 1000)
        } catch (_: Exception) {
            entry.copy(taken = entry.taken.takeIf { it > 0 } ?: entry.modified * 1000)
        }
    }

    internal fun exifTime(
        date: String?,
        offset: String?,
        zone: ZoneId = ZoneId.systemDefault(),
    ): Long? =
        try {
            if (date == null) null
            else
                LocalDateTime.parse(
                        date,
                        DateTimeFormatter.ofPattern("uuuu:MM:dd HH:mm:ss")
                            .withResolverStyle(ResolverStyle.STRICT),
                    )
                    .atZone(offset?.let { ZoneOffset.of(it) } ?: zone)
                    .toInstant()
                    .toEpochMilli()
        } catch (_: Exception) {
            null
        }
}

/** Prefer the installed Samsung gallery. Other phones choose their native content provider. */
class NativeGalleryContract : ActivityResultContract<Unit, List<Uri>>() {
    override fun createIntent(context: Context, input: Unit): Intent {
        val intent =
            Intent(Intent.ACTION_GET_CONTENT).apply {
                type = "image/*"
                putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                addFlags(
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or
                        Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
                )
            }
        val samsung = Intent(intent).setPackage("com.sec.android.gallery3d")
        return if (samsung.resolveActivity(context.packageManager) != null) samsung
        else Intent.createChooser(intent, "Choose a gallery")
    }

    override fun parseResult(resultCode: Int, intent: Intent?): List<Uri> {
        if (resultCode != Activity.RESULT_OK || intent == null) return emptyList()
        return buildList {
                intent.data?.let { add(it) }
                intent.clipData?.let { clip ->
                    for (i in 0 until clip.itemCount) add(clip.getItemAt(i).uri)
                }
            }
            .distinct()
    }
}
