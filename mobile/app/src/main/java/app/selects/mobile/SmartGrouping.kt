package app.selects.mobile

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlin.math.*

data class SmartGroup(val id: Long, val name: String, val reason: String, val photos: List<Photo>)

object SmartGrouping {
    private const val HOUR = 3_600_000L

    fun suggest(
        photos: List<Photo>,
        daily: Boolean = false,
        zone: ZoneId = ZoneId.systemDefault(),
    ): List<SmartGroup> {
        val result = mutableListOf<SmartGroup>()
        val dates = DateTimeFormatter.ofPattern("d MMM yyyy").withZone(zone)
        // An album boundary keeps screenshots/downloads out of camera trips. IDs distinguish
        // same-name albums.
        photos
            .groupBy { it.albumId.ifBlank { it.album } }
            .values
            .forEach { album ->
                val chunks = mutableListOf<MutableList<Photo>>()
                for (photo in album.sortedWith(compareBy<Photo> { it.taken }.thenBy { it.id })) {
                    val previous = chunks.lastOrNull()?.lastOrNull()
                    val anchor =
                        chunks.lastOrNull()?.firstOrNull {
                            it.latitude != null && it.longitude != null
                        }
                    val gap = if (previous != null) photo.taken - previous.taken else 0
                    val newDay =
                        previous != null &&
                            photo.taken > 0 &&
                            previous.taken > 0 &&
                            Instant.ofEpochMilli(photo.taken).atZone(zone).toLocalDate() !=
                                Instant.ofEpochMilli(previous.taken).atZone(zone).toLocalDate()
                    val far =
                        anchor != null &&
                            photo.latitude != null &&
                            photo.longitude != null &&
                            distanceKm(anchor, photo) > 50
                    val boundary =
                        previous == null ||
                            (photo.taken == 0L) != (previous.taken == 0L) ||
                            gap > 12 * HOUR ||
                            (newDay && (daily || gap > 4 * HOUR)) ||
                            far
                    if (boundary) chunks.add(mutableListOf())
                    chunks.last().add(photo)
                }
                chunks.forEach { chunk ->
                    val first = chunk.first()
                    val last = chunk.last()
                    val date =
                        if (first.taken > 0) dates.format(Instant.ofEpochMilli(first.taken))
                        else "Unknown date"
                    val end =
                        if (last.taken > 0) dates.format(Instant.ofEpochMilli(last.taken)) else date
                    val range = if (date == end) date else "$date to $end"
                    val located = chunk.count { it.latitude != null && it.longitude != null }
                    result.add(
                        SmartGroup(
                            first.id,
                            "${first.album.ifBlank { "Photos" }} · $range",
                            if (located > 0)
                                "Capture times and nearby locations; $located photos with GPS"
                            else "Album and available dates; no location inferred",
                            chunk,
                        )
                    )
                }
            }
        return result.sortedByDescending { it.photos.maxOfOrNull { p -> p.taken } ?: 0 }
    }

    private fun distanceKm(a: Photo, b: Photo): Double {
        val x = Math.toRadians(a.latitude!!)
        val y = Math.toRadians(b.latitude!!)
        val dLat = y - x
        val dLon = Math.toRadians(b.longitude!! - a.longitude!!)
        val h = sin(dLat / 2).pow(2) + cos(x) * cos(y) * sin(dLon / 2).pow(2)
        return 6371 * 2 * asin(sqrt(h.coerceIn(0.0, 1.0)))
    }
}
