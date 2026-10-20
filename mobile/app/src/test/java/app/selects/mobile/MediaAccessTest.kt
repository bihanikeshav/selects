package app.selects.mobile

import android.Manifest
import android.app.Activity
import android.app.Application
import android.content.ClipData
import android.content.Intent
import android.net.Uri
import androidx.test.core.app.ApplicationProvider
import java.time.Instant
import java.time.ZoneId
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class MediaAccessTest {
    @Test
    fun unchangedMetadataUsesCacheWithoutOpeningTheOriginal() {
        val app = ApplicationProvider.getApplicationContext<Application>()
        val entry =
            MediaEntry(
                "content://media/external/images/media/1",
                "photo",
                bytes = 100,
                modified = 1,
            )
        val cached =
            Photo(
                1,
                entry.uri,
                entry.name,
                "catalog",
                null,
                null,
                null,
                "keep",
                null,
                taken = 123,
                bytes = 100,
                modified = 1,
                latitude = 12.0,
                longitude = 34.0,
                metadataVersion = 2,
            )
        val result = MediaAccess.metadata(app, entry, cached, locationAllowed = true)
        assertTrue(result.metadataRead)
        assertEquals(123, result.taken)
        assertEquals(12.0, result.latitude!!, 0.0)
        assertEquals(34.0, result.longitude!!, 0.0)
        assertNull(
            MediaAccess.metadata(app, entry.copy(modified = 2), cached, locationAllowed = true)
                .latitude
        )
    }

    @Test
    fun partialPhotoAccessIsNotReportedAsWholePhoneAccess() {
        val app = ApplicationProvider.getApplicationContext<Application>()
        Shadows.shadowOf(app).grantPermissions(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED)
        assertTrue(MediaAccess.any(app))
        assertFalse(MediaAccess.full(app))
        Shadows.shadowOf(app).grantPermissions(Manifest.permission.READ_MEDIA_IMAGES)
        assertTrue(MediaAccess.full(app))
    }

    @Test
    fun nativeSelectionHandlesMultipleResultsAndCancellation() {
        val a = Uri.parse("content://media/external/images/media/1")
        val b = Uri.parse("content://media/external/images/media/2")
        val clip = ClipData.newRawUri("photos", a)
        clip.addItem(ClipData.Item(b))
        val intent = Intent().setData(a).apply { clipData = clip }
        val contract = NativeGalleryContract()
        assertEquals(listOf(a, b), contract.parseResult(Activity.RESULT_OK, intent))
        assertTrue(contract.parseResult(Activity.RESULT_CANCELED, intent).isEmpty())
    }

    @Test
    fun pickerAndFileProviderUrisAreNotTrashableMediaStoreOriginals() {
        assertTrue(
            MediaAccess.direct(Uri.parse("content://media/external_primary/images/media/12"))
        )
        assertFalse(
            MediaAccess.direct(
                Uri.parse(
                    "content://media/picker/0/com.android.providers.media.photopicker/media/12"
                )
            )
        )
        assertFalse(MediaAccess.direct(Uri.parse("content://some.gallery.fileprovider/photo/12")))
    }

    @Test
    fun exifTimeUsesTheEmbeddedOffsetAndRejectsInvalidDates() {
        assertEquals(
            Instant.parse("2026-10-01T06:30:00Z").toEpochMilli(),
            MediaAccess.exifTime("2026:10:01 12:00:00", "+05:30"),
        )
        assertNull(MediaAccess.exifTime("2026:02:30 12:00:00", null))
        assertNull(MediaAccess.exifTime(null, null))
        assertEquals(
            Instant.parse("2026-10-01T12:00:00Z").toEpochMilli(),
            MediaAccess.exifTime("2026:10:01 12:00:00", null, ZoneId.of("UTC")),
        )
    }
}
