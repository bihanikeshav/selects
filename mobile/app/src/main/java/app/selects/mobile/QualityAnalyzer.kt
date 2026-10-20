package app.selects.mobile

import kotlin.math.ln
import kotlin.math.roundToInt

data class Quality(val score: Int, val detail: Double, val clipped: Double) {
    val reason: String
        get() =
            when {
                clipped > 0.35 -> "Large areas near black or white"
                detail < 0.0005 -> "Low detail, check focus"
                else -> "Detail and exposure look balanced"
            }
}

/** Technical signals from a bounded preview, not an aesthetic or subject judgment. */
object QualityAnalyzer {
    const val VERSION = 2

    fun analyze(width: Int, height: Int, pixels: IntArray): Quality {
        require(width >= 3 && height >= 3 && pixels.size == width * height)
        val luma =
            DoubleArray(pixels.size) { i ->
                val p = pixels[i]
                (0.2126 * ((p shr 16) and 255) +
                    0.7152 * ((p shr 8) and 255) +
                    0.0722 * (p and 255)) / 255.0
            }
        val clipped = luma.count { it < 0.02 || it > 0.98 }.toDouble() / luma.size
        var sum = 0.0
        var squares = 0.0
        val count = (width - 2) * (height - 2)
        for (y in 1 until height - 1) for (x in 1 until width - 1) {
            val i = y * width + x
            val lap = luma[i - 1] + luma[i + 1] + luma[i - width] + luma[i + width] - 4 * luma[i]
            sum += lap
            squares += lap * lap
        }
        val detail = (squares / count - (sum / count) * (sum / count)).coerceAtLeast(0.0)
        val sharpness = (ln(1 + detail * 1000) / ln(101.0)).coerceIn(0.0, 1.0)
        val score = (100 * (0.7 * sharpness + 0.3 * (1 - clipped))).roundToInt()
        return Quality(score.coerceIn(0, 100), detail, clipped)
    }
}
