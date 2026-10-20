package app.selects.mobile

import org.junit.Assert.*
import org.junit.Test

class QualityAnalyzerTest {
    @Test fun detailedImageRanksAboveFlatImage() {
        val flat = QualityAnalyzer.analyze(32, 32, IntArray(1024) { 0xff808080.toInt() })
        val detailed = QualityAnalyzer.analyze(32, 32, IntArray(1024) {
            if ((it / 32 + it % 32) % 2 == 0) 0xff404040.toInt() else 0xffb0b0b0.toInt()
        })
        assertTrue(detailed.score > flat.score)
        assertTrue(detailed.detail > flat.detail)
        assertTrue(flat.reason.contains("focus"))
    }

    @Test fun clippedImageIsFlaggedWithoutNaN() {
        val result = QualityAnalyzer.analyze(3, 3, IntArray(9) { 0xffffffff.toInt() })
        assertEquals(1.0, result.clipped, 0.0)
        assertEquals(0, result.score)
        assertFalse(result.detail.isNaN())
        assertTrue(result.reason.contains("black or white"))
    }

    @Test(expected = IllegalArgumentException::class)
    fun invalidDimensionsAreRejected() {
        QualityAnalyzer.analyze(10, 10, IntArray(9))
    }
}
