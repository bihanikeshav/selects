package app.selects.mobile

import android.graphics.ImageDecoder
import kotlin.math.max

internal object PhotoProcessor {
    fun process(store: PhotoStore, photo: Photo, source: ImageDecoder.Source) {
        // ImageDecoder honors EXIF orientation and bounds the decode allocation.
        val bitmap =
            ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                val scale = (640.0 / max(info.size.width, info.size.height)).coerceAtMost(1.0)
                decoder.setTargetSize(
                    max(3, (info.size.width * scale).toInt()),
                    max(3, (info.size.height * scale).toInt()),
                )
            }
        try {
            val pixels = IntArray(bitmap.width * bitmap.height)
            bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
            val quality = QualityAnalyzer.analyze(bitmap.width, bitmap.height, pixels)
            store.complete(
                photo.id,
                null,
                quality,
                VisualFingerprint.create(bitmap.width, bitmap.height, pixels),
            )
        } finally {
            bitmap.recycle()
        }
    }
}
