package app.selects.mobile

import android.content.Context
import android.graphics.Bitmap
import android.graphics.ImageDecoder
import java.io.File
import java.io.IOException
import kotlin.math.max

internal object PhotoProcessor {
    fun process(context: Context, store: PhotoStore, photo: Photo, source: ImageDecoder.Source) {
        // ImageDecoder honors EXIF orientation and bounds the decode allocation.
        val bitmap = ImageDecoder.decodeBitmap(
            source,
        ) { decoder, info, _ ->
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            val scale = (640.0 / max(info.size.width, info.size.height)).coerceAtMost(1.0)
            decoder.setTargetSize(max(3, (info.size.width * scale).toInt()),
                max(3, (info.size.height * scale).toInt()))
        }
        try {
            val pixels = IntArray(bitmap.width * bitmap.height)
            bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
            val quality = QualityAnalyzer.analyze(bitmap.width, bitmap.height, pixels)
            val previews = File(context.filesDir, "previews").apply { mkdirs() }
            val destination = File(previews, "${photo.id}.jpg")
            val temp = File(previews, "${photo.id}.tmp")
            try {
                temp.outputStream().use { output ->
                    if (!bitmap.compress(Bitmap.CompressFormat.JPEG, 85, output)) {
                        throw IOException("Could not save the preview")
                    }
                    output.fd.sync()
                }
                if (!temp.renameTo(destination)) throw IOException("Could not save the preview")
            } finally { temp.delete() }
            store.complete(photo.id, destination.absolutePath, quality)
        } finally { bitmap.recycle() }
    }
}
