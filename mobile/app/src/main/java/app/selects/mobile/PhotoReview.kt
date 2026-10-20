package app.selects.mobile

import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.os.Build
import android.util.Size
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.net.toUri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Composable
internal fun PhotoTile(photo: Photo, decide: (Photo, String) -> Unit, open: () -> Unit) {
    val context = LocalContext.current
    val bitmap by
        produceState<ImageBitmap?>(null, photo.uri, photo.status) {
            value =
                withContext(Dispatchers.IO) {
                    try {
                        val legacy = photo.preview?.let { BitmapFactory.decodeFile(it) }
                        (legacy
                                ?: if (
                                    Build.VERSION.SDK_INT >= 29 &&
                                        MediaAccess.direct(photo.uri.toUri())
                                ) {
                                    context.contentResolver.loadThumbnail(
                                        photo.uri.toUri(),
                                        Size(400, 400),
                                        null,
                                    )
                                } else
                                    ImageDecoder.decodeBitmap(
                                        ImageDecoder.createSource(
                                            context.contentResolver,
                                            photo.uri.toUri(),
                                        )
                                    ) { decoder, info, _ ->
                                        decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                                        val scale =
                                            (400.0 / maxOf(info.size.width, info.size.height))
                                                .coerceAtMost(1.0)
                                        decoder.setTargetSize(
                                            maxOf(1, (info.size.width * scale).toInt()),
                                            maxOf(1, (info.size.height * scale).toInt()),
                                        )
                                    })
                            .asImageBitmap()
                    } catch (_: Exception) {
                        null
                    }
                }
        }
    Column {
        Surface(
            Modifier.fillMaxWidth()
                .aspectRatio(1f)
                .clickable(onClickLabel = "Review ${photo.name}", onClick = open),
            color = MaterialTheme.colorScheme.surfaceVariant,
            shape = MaterialTheme.shapes.small,
        ) {
            val image = bitmap
            if (image != null)
                Image(image, contentDescription = photo.name, contentScale = ContentScale.Crop)
            else
                Box(contentAlignment = Alignment.Center) {
                    Text(if (photo.status == "queued") "Loading photo" else "Preview unavailable")
                }
        }
        Text(
            photo.name,
            maxLines = 1,
            style = MaterialTheme.typography.titleSmall,
            modifier = Modifier.padding(top = 8.dp),
        )
        Text(
            photo.error ?: photo.reason ?: "Waiting for analysis",
            style = MaterialTheme.typography.bodySmall,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            FilterChip(
                selected = photo.decision == "keep",
                onClick = { decide(photo, if (photo.decision == "keep") "unreviewed" else "keep") },
                label = { Text("Keep") },
            )
            FilterChip(
                selected = photo.decision == "reject",
                onClick = {
                    decide(photo, if (photo.decision == "reject") "unreviewed" else "reject")
                },
                label = { Text("Reject") },
            )
        }
    }
}

@Composable
internal fun ReviewPhoto(photo: Photo, decide: (Photo, String) -> Unit) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val source by
        produceState<Pair<Boolean, ImageBitmap?>>(true to null, photo.uri) {
            val bitmap =
                withContext(Dispatchers.IO) {
                    try {
                        android.graphics.ImageDecoder.decodeBitmap(
                                android.graphics.ImageDecoder.createSource(
                                    context.contentResolver,
                                    photo.uri.toUri(),
                                )
                            ) { decoder, info, _ ->
                                decoder.allocator = android.graphics.ImageDecoder.ALLOCATOR_SOFTWARE
                                val scale =
                                    (1600.0 / maxOf(info.size.width, info.size.height))
                                        .coerceAtMost(1.0)
                                decoder.setTargetSize(
                                    maxOf(1, (info.size.width * scale).toInt()),
                                    maxOf(1, (info.size.height * scale).toInt()),
                                )
                            }
                            .asImageBitmap()
                    } catch (_: Exception) {
                        null
                    }
                }
            value = false to bitmap
        }
    // The source can be revoked independently of the saved review and preview.
    Column(Modifier.fillMaxSize()) {
        Text(photo.name, style = MaterialTheme.typography.titleMedium)
        Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
            source.second?.let {
                Image(
                    it,
                    contentDescription = photo.name,
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.Fit,
                )
            }
                ?: if (source.first) {
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                } else Text("Photo unavailable. Re-select it to restore access.")
        }
        Text(
            photo.error ?: photo.reason ?: "Waiting for analysis",
            modifier = Modifier.padding(vertical = 12.dp),
        )
        Row(
            Modifier.fillMaxWidth().padding(bottom = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            FilterChip(
                selected = photo.decision == "keep",
                onClick = { decide(photo, if (photo.decision == "keep") "unreviewed" else "keep") },
                label = { Text("Keep") },
            )
            FilterChip(
                selected = photo.decision == "reject",
                onClick = {
                    decide(photo, if (photo.decision == "reject") "unreviewed" else "reject")
                },
                label = { Text("Reject") },
            )
        }
    }
}
