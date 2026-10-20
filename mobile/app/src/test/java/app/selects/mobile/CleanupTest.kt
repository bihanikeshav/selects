package app.selects.mobile

import java.util.Random
import org.junit.Assert.*
import org.junit.Test

class CleanupTest {
    @Test
    fun keptLegacyPickerReferencesProtectTheSameGalleryOriginal() {
        val original = photo(1, "same")
        val picked =
            original.copy(
                id = 99,
                uri = "content://media/picker/old",
                source = "picked",
                decision = "keep",
                bytes = 0,
            )
        val copy = photo(2, "same")
        val group = Cleanup.suggestions(listOf(original, picked, copy)).single()
        assertEquals(1L, group.keeper!!.id)
        assertEquals("keep", group.keeper!!.decision)
        assertEquals(
            listOf(2L),
            Cleanup.allowedSelection(listOf(group), setOf(1, 2, 99)).map { it.id },
        )
    }

    private fun photo(
        id: Long,
        sha: String? = null,
        decision: String = "unreviewed",
        color: String = "444444",
        detail: Double = .01,
    ) =
        Photo(
            id,
            "content://media/external/images/media/$id",
            "$id.jpg",
            "ready",
            "preview",
            (100 - id).toInt(),
            "detail",
            decision,
            null,
            bytes = 100,
            source = "gallery",
            sha = sha,
            visualHash = "1",
            visual = color.repeat(64),
            detail = detail,
            clipped = 0.0,
            aspect = 1.5,
        )

    @Test
    fun exactCopiesProtectKeptPhotoEvenWhenAnotherCopyScoresHigher() {
        val groups =
            Cleanup.suggestions(
                listOf(
                    photo(1, "same"),
                    photo(2, "same", "keep"),
                    photo(3, "other", color = "ffffff"),
                )
            )
        val exact = groups.single { it.kind == "Exact duplicates" }
        assertEquals(2L, exact.keeper!!.id)
        assertEquals(listOf(1L), exact.candidates.map { it.id })
        assertEquals(listOf(1L), Cleanup.allowedSelection(groups, setOf(1, 2, 3)).map { it.id })
    }

    @Test
    fun allKeptCopiesRemainProtected() {
        assertTrue(
            Cleanup.suggestions(listOf(photo(1, "same", "keep"), photo(2, "same", "keep")))
                .isEmpty()
        )
    }

    @Test
    fun hashCollisionWithDifferentColorsIsNotASimilarPhoto() {
        assertTrue(
            Cleanup.suggestions(listOf(photo(1, color = "111111"), photo(2, color = "eeeeee")))
                .isEmpty()
        )
    }

    @Test
    fun nearDuplicateClustersDoNotChainThroughAnIntermediateImage() {
        val groups =
            Cleanup.suggestions(
                listOf(
                    photo(1, color = "444444"),
                    photo(2, color = "555555"),
                    photo(3, color = "666666"),
                )
            )
        assertEquals(listOf(2L), groups.single().candidates.map { it.id })
        assertEquals(1L, groups.single().keeper!!.id)
    }

    @Test
    fun lowDetailIsSeparateFromSimilarityAndKeptImagesAreExcluded() {
        val groups =
            Cleanup.suggestions(
                listOf(photo(1, detail = .00001), photo(2, decision = "keep", detail = .00001))
            )
        assertEquals("Very low detail", groups.single().kind)
        assertEquals(listOf(1L), groups.single().candidates.map { it.id })
    }

    @Test
    fun keeperInAnyGroupIsProtectedAcrossAllSelectionPaths() {
        val keeper = photo(1)
        val other = photo(2)
        val groups =
            listOf(
                CleanupGroup("Exact duplicates", keeper, listOf(other)),
                CleanupGroup("Similar photos", other, listOf(keeper)),
            )
        assertTrue(Cleanup.allowedSelection(groups, setOf(1, 2)).isEmpty())
    }

    @Test
    fun hashIndexMatchesBruteForceWithoutMissingNeighbors() {
        val random = Random(7)
        val values = (1L..2000L).associateWith { random.nextLong() }
        val index = HashIndex()
        values.forEach { (id, hash) -> index.add(hash, id) }
        values.entries.take(50).forEach { (_, hash) ->
            val changed = hash xor 7L
            val expected = values.filterValues { java.lang.Long.bitCount(it xor changed) <= 5 }.keys
            assertEquals(expected, index.matches(changed, 5).toSet())
        }
    }
}
