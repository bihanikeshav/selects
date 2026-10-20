package app.selects.mobile

import java.time.Instant
import java.time.ZoneId
import org.junit.Assert.*
import org.junit.Test

class SmartGroupingTest {
    private fun photo(
        id: Long,
        date: String?,
        album: String = "camera",
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
            taken = date?.let { Instant.parse(it).toEpochMilli() } ?: 0,
            album = album,
            albumId = album,
            latitude = lat,
            longitude = lon,
            imported = false,
        )

    @Test
    fun overnightEventsStayTogetherUntilUserChoosesDailySplit() {
        val photos = listOf(photo(1, "2026-10-01T23:50:00Z"), photo(2, "2026-10-02T00:10:00Z"))
        assertEquals(1, SmartGrouping.suggest(photos, zone = ZoneId.of("UTC")).size)
        assertEquals(2, SmartGrouping.suggest(photos, true, ZoneId.of("UTC")).size)
    }

    @Test
    fun datesAlbumsAndMissingDatesProduceSeparateSuggestionsWithoutDroppingPhotos() {
        val photos =
            listOf(
                photo(1, "2026-10-01T10:00:00Z"),
                photo(2, "2026-10-03T10:00:00Z"),
                photo(3, null),
                photo(4, null, "screenshots"),
            )
        val groups = SmartGrouping.suggest(photos)
        assertEquals(4, groups.size)
        assertEquals(
            photos.map { it.id }.toSet(),
            groups.flatMap { it.photos }.map { it.id }.toSet(),
        )
        assertTrue(groups.any { "Unknown date" in it.name })
    }

    @Test
    fun missingGpsDoesNotBridgeDistantLocations() {
        val photos =
            listOf(
                photo(1, "2026-10-01T10:00:00Z", lat = 0.0, lon = 0.0),
                photo(2, "2026-10-01T10:10:00Z"),
                photo(3, "2026-10-01T10:20:00Z", lat = 0.0, lon = 2.0),
            )
        val groups = SmartGrouping.suggest(photos)
        assertEquals(
            listOf(1L, 2L),
            groups.first { it.photos.any { p -> p.id == 1L } }.photos.map { it.id },
        )
        assertEquals(2, groups.size)
    }

    @Test
    fun absentGpsNeverInventsALocation() {
        assertTrue(
            SmartGrouping.suggest(listOf(photo(1, null)))
                .single()
                .reason
                .contains("no location inferred")
        )
    }
}
