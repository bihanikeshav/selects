package app.selects.mobile

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.PriorityQueue
import kotlin.math.*

data class SmartGroup(val id: Long, val name: String, val reason: String, val photos: List<Photo>)

/** Broad, reviewable collections. Existing personal folders are never sliced into days. */
object SmartGrouping {
    private const val DAY = 86_400_000L
    const val DEFAULT_LIMIT = 50

    fun suggest(
        photos: List<Photo>,
        zone: ZoneId = ZoneId.systemDefault(),
        maxGroups: Int = DEFAULT_LIMIT,
    ): List<SmartGroup> {
        require(maxGroups > 0)
        if (photos.isEmpty()) return emptyList()
        val result =
            photos
                .filterNot(AlbumSources::camera)
                .groupBy { it.albumId.ifBlank { it.album } }
                .values
                .map { folder ->
                    SmartGroup(
                        folder.minOf { it.id },
                        folder.first().album.ifBlank { "Selected folder" },
                        "Your folder, kept together",
                        folder.sortedWith(compareBy<Photo> { it.taken }.thenBy { it.id }),
                    )
                }
                .toMutableList()
        val camera =
            photos
                .filter(AlbumSources::camera)
                .sortedWith(compareBy<Photo> { it.taken }.thenBy { it.id })
        val unknown = camera.filter { it.taken <= 0 }
        if (unknown.isNotEmpty())
            result.add(
                SmartGroup(
                    unknown.minOf { it.id },
                    "Camera · Unknown date",
                    "Capture date unavailable; kept separate",
                    unknown,
                )
            )
        val dated = camera.filter { it.taken > 0 }
        // Named folders can themselves exceed the overview budget. Preserve those deliberate
        // folders instead of silently merging them to satisfy an arbitrary number.
        val budget = (maxGroups - result.size).coerceAtLeast(1)
        val chunks = broadEvents(dated, budget)
        val dates = DateTimeFormatter.ofPattern("d MMM yyyy").withZone(zone)
        chunks.forEach { chunk ->
            val from = dates.format(Instant.ofEpochMilli(chunk.first().taken))
            val to = dates.format(Instant.ofEpochMilli(chunk.last().taken))
            val gps = chunk.count { it.latitude != null && it.longitude != null }
            result.add(
                SmartGroup(
                    chunk.minOf { it.id },
                    "Camera · " + if (from == to) from else "$from to $to",
                    if (gps > 0) "Dates and available locations; related periods kept together"
                    else "Broad date group; no location inferred",
                    chunk,
                )
            )
        }
        return result.sortedWith(
            compareByDescending<SmartGroup> { it.photos.maxOf { p -> p.taken } }.thenBy { it.id }
        )
    }

    private class Chunk(val start: Int, var end: Int, var location: Photo?) {
        var previous: Chunk? = null
        var next: Chunk? = null
        var revision = 0
        var removed = false
    }

    private data class Edge(
        val left: Chunk,
        val right: Chunk,
        val leftRevision: Int,
        val rightRevision: Int,
        val cost: Double,
    )

    private fun broadEvents(photos: List<Photo>, budget: Int): List<List<Photo>> {
        if (photos.isEmpty()) return emptyList()
        val chunks = mutableListOf<Chunk>()
        var start = 0
        var anchor: Photo? = null
        for (i in photos.indices) {
            val p = photos[i]
            val previous = photos.getOrNull(i - 1)
            val gap = if (previous == null) 0 else p.taken - previous.taken
            val located = p.latitude != null && p.longitude != null
            val distant = anchor != null && located && gap > DAY && distanceKm(anchor, p) > 300
            if (
                i > start && (gap > 3 * DAY || p.taken - photos[start].taken > 21 * DAY || distant)
            ) {
                chunks.add(Chunk(start, i, anchor))
                start = i
                anchor = null
            }
            if (anchor == null && located) anchor = p
        }
        chunks.add(Chunk(start, photos.size, anchor))
        chunks.forEachIndexed { i, c ->
            c.previous = chunks.getOrNull(i - 1)
            c.next = chunks.getOrNull(i + 1)
        }
        val queue = PriorityQueue<Edge>(compareBy<Edge> { it.cost }.thenBy { it.left.start })
        fun offer(left: Chunk?) {
            val right = left?.next ?: return
            val gap = (photos[right.start].taken - photos[left.end - 1].taken).toDouble() / DAY
            val span = (photos[right.end - 1].taken - photos[left.start].taken).toDouble() / DAY
            val distance =
                if (left.location != null && right.location != null)
                    distanceKm(left.location!!, right.location!!)
                else 0.0
            // Merge neighboring periods with the smallest date/location separation first.
            queue.add(
                Edge(
                    left,
                    right,
                    left.revision,
                    right.revision,
                    gap * 2 + span * .15 + min(distance, 3000.0) / 30,
                )
            )
        }
        chunks.forEach(::offer)
        var count = chunks.size
        while (count > budget && queue.isNotEmpty()) {
            val edge = queue.remove()
            val left = edge.left
            val right = edge.right
            if (
                left.removed ||
                    right.removed ||
                    left.next !== right ||
                    left.revision != edge.leftRevision ||
                    right.revision != edge.rightRevision
            )
                continue
            left.end = right.end
            left.location = left.location ?: right.location
            left.next = right.next
            right.next?.previous = left
            left.revision++
            right.removed = true
            count--
            offer(left.previous)
            offer(left)
        }
        return chunks.filterNot { it.removed }.map { photos.subList(it.start, it.end) }
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
