package app.selects.mobile

import java.time.Instant
import java.time.ZoneId
import org.junit.Assert.*
import org.junit.Test

class SmartGroupingTest {
    private fun photo(
        id: Long,
        date: Long,
        album: String = "Camera",
        lat: Double? = null,
        lon: Double? = null,
    ) =
        Photo(
            id,
            "content://media/external/images/media/$id",
            "$id.jpg",
            "catalog",
            null,
            null,
            null,
            "unreviewed",
            null,
            taken = date,
            album = album,
            albumId = album,
            latitude = lat,
            longitude = lon,
            imported = false,
        )

    private val day = 86_400_000L
    private val start = Instant.parse("2023-01-01T00:00:00Z").toEpochMilli()

    @Test
    fun namedTripFolderStaysWholeAcrossDatesAndLocations() {
        val photos =
            listOf(
                photo(1, start, "Ladakh", 0.0, 0.0),
                photo(2, start + 20 * day, "Ladakh", 5.0, 5.0),
            )
        val groups = SmartGrouping.suggest(photos)
        assertEquals(1, groups.size)
        assertEquals("Ladakh", groups.single().name)
        assertEquals(2, groups.single().photos.size)
    }

    @Test
    fun overnightAndMultiDayTripDoesNotBecomeDailyAlbums() {
        val photos = (0..8).map { photo(it.toLong(), start + it * day) }
        assertEquals(1, SmartGrouping.suggest(photos).size)
    }

    @Test
    fun cameraAndExpertRawShareTheSameEvent() {
        val groups =
            SmartGrouping.suggest(listOf(photo(1, start), photo(2, start + 1000, "Expert RAW")))
        assertEquals(1, groups.size)
        assertEquals(2, groups.single().photos.size)
    }

    @Test
    fun thirtyFiveThousandPhotosHaveAtMostFiftyBroadSuggestionsWithoutLossOrDuplicates() {
        val photos =
            (1..35000).map { id ->
                photo(
                    id.toLong(),
                    start + (id / 25) * day,
                    if (id % 7 == 0) "Trip ${id%9}"
                    else if (id % 2 == 0) "Expert RAW" else "Camera",
                    if (id % 5 == 0) null else 20.0 + (id / 2500),
                    if (id % 5 == 0) null else 70.0 + (id / 2500),
                )
            }
        val groups = SmartGrouping.suggest(photos)
        assertTrue(groups.size <= 50)
        assertEquals(photos.size, groups.sumOf { it.photos.size })
        assertEquals(
            photos.map { it.id }.toSet(),
            groups.flatMap { it.photos }.map { it.id }.toSet(),
        )
        assertEquals(9, groups.count { it.name.startsWith("Trip ") })
    }

    @Test
    fun distantSeparatedTripsStaySeparateWhenThereIsRoom() {
        val groups =
            SmartGrouping.suggest(
                listOf(
                    photo(1, start, lat = 0.0, lon = 0.0),
                    photo(2, start + 2 * day, lat = 0.0, lon = 20.0),
                )
            )
        assertEquals(2, groups.size)
    }

    @Test
    fun missingDatesStaySeparateAndMissingGpsIsNotInvented() {
        val groups = SmartGrouping.suggest(listOf(photo(1, 0), photo(2, start)))
        assertEquals(2, groups.size)
        assertTrue(groups.any { "Unknown date" in it.name })
        assertTrue(
            groups.first { it.photos.single().id == 2L }.reason.contains("no location inferred")
        )
    }

    @Test
    fun folderIdsKeepSameNameUserFoldersDistinct() {
        val a = photo(1, start, "Travel")
        assertEquals(2, SmartGrouping.suggest(listOf(a, a.copy(id = 2, albumId = "second"))).size)
    }

    @Test
    fun budgetMergesClosestAdjacentPeriodsFirstAndIsDeterministic() {
        val photos = listOf(photo(1, start), photo(2, start + 5 * day), photo(3, start + 200 * day))
        val groups = SmartGrouping.suggest(photos, ZoneId.of("UTC"), 2)
        assertEquals(
            setOf(1L, 2L),
            groups.first { it.photos.any { p -> p.id == 1L } }.photos.map { it.id }.toSet(),
        )
        assertEquals(groups, SmartGrouping.suggest(photos.reversed(), ZoneId.of("UTC"), 2))
    }

    @Test
    fun existingUserFoldersArePreservedEvenWhenTheyExceedTheCameraBudget() {
        val groups = SmartGrouping.suggest((1..55).map { photo(it.toLong(), start, "Folder $it") })
        assertEquals(55, groups.size)
        assertTrue(groups.all { it.reason == "Your folder, kept together" })
    }
}
