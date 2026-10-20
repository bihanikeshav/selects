package app.selects.mobile

import kotlin.math.abs

data class VisualSignature(val hash: String, val colors: String, val aspect: Double)

data class CleanupGroup(val kind: String, val keeper: Photo?, val candidates: List<Photo>)

object VisualFingerprint {
    fun create(width: Int, height: Int, pixels: IntArray): VisualSignature {
        require(width > 0 && height > 0 && pixels.size == width * height)
        fun pixel(x: Int, y: Int, w: Int, h: Int): Int =
            pixels[
                (y * height / h).coerceAtMost(height - 1) * width +
                    (x * width / w).coerceAtMost(width - 1)]
        fun luma(p: Int): Int =
            ((p shr 16 and 255) * 299 + (p shr 8 and 255) * 587 + (p and 255) * 114) / 1000
        var hash = 0L
        for (y in 0..7) for (x in 0..7) hash =
            (hash shl 1) or if (luma(pixel(x, y, 9, 8)) > luma(pixel(x + 1, y, 9, 8))) 1L else 0L
        val colors = buildString {
            for (y in 0..7) for (x in 0..7) append("%06x".format(pixel(x, y, 8, 8) and 0xffffff))
        }
        return VisualSignature(
            java.lang.Long.toUnsignedString(hash, 16),
            colors,
            width.toDouble() / height,
        )
    }
}

/**
 * Hamming-distance index. Near matches also need compatible color, aspect, and real image detail.
 */
internal class HashIndex {
    private data class Node(
        val hash: Long,
        val ids: MutableList<Long>,
        val children: MutableMap<Int, Node> = mutableMapOf(),
    )

    private var root: Node? = null

    fun add(hash: Long, id: Long) {
        var node = root
        if (node == null) {
            root = Node(hash, mutableListOf(id))
            return
        }
        while (node != null) {
            val distance = java.lang.Long.bitCount(node.hash xor hash)
            if (distance == 0) {
                node.ids.add(id)
                return
            }
            val next = node.children[distance]
            if (next == null) {
                node.children[distance] = Node(hash, mutableListOf(id))
                return
            }
            node = next
        }
    }

    fun matches(hash: Long, radius: Int): List<Long> {
        val result = mutableListOf<Long>()
        val stack = java.util.ArrayDeque<Node>()
        root?.let { stack.add(it) }
        while (stack.isNotEmpty()) {
            val node = stack.removeLast()
            val d = java.lang.Long.bitCount(node.hash xor hash)
            if (d <= radius) result.addAll(node.ids)
            for ((edge, child) in node.children) if (edge in d - radius..d + radius)
                stack.add(child)
        }
        return result
    }
}

object Cleanup {
    fun suggestions(photos: List<Photo>): List<CleanupGroup> {
        // Old picker/provider URIs can alias a MediaStore original. Conservatively protect matching
        // names
        // and sizes, rather than risk suggesting a previously kept photo through a second URI.
        val legacyKeeps = photos.filter { it.source != "gallery" && it.decision == "keep" }
        val eligible =
            photos
                .filter { it.source == "gallery" && it.status == "ready" }
                .map { p ->
                    if (
                        legacyKeeps.any {
                            it.name == p.name && (it.bytes == 0L || it.bytes == p.bytes)
                        }
                    )
                        p.copy(decision = "keep")
                    else p
                }
                .sortedWith(
                    compareByDescending<Photo> { it.decision == "keep" }
                        .thenByDescending { it.score ?: 0 }
                        .thenByDescending { it.bytes }
                        .thenBy { it.id }
                )
        val result = mutableListOf<CleanupGroup>()
        val assigned = mutableSetOf<Long>()
        eligible
            .filter { it.sha != null }
            .groupBy { it.sha }
            .values
            .filter { it.size > 1 }
            .forEach { group ->
                val keeper = group.first()
                val extras = group.drop(1).filter { it.decision != "keep" }
                if (extras.isNotEmpty()) {
                    result.add(CleanupGroup("Exact duplicates", keeper, extras))
                    assigned.addAll(extras.map { it.id })
                }
            }
        val index = HashIndex()
        val anchors = mutableMapOf<Long, Photo>()
        val clusters = linkedMapOf<Long, MutableList<Photo>>()
        for (photo in
            eligible.filter {
                it.id !in assigned &&
                    (it.detail ?: 0.0) >= 0.0005 &&
                    it.visualHash != null &&
                    it.visual != null
            }) {
            val hash = photo.visualHash!!.toULongOrNull(16)?.toLong() ?: continue
            val keeper =
                index
                    .matches(hash, 5)
                    .asSequence()
                    .mapNotNull { anchors[it] }
                    .firstOrNull { similar(it, photo) }
            if (keeper != null && photo.decision != "keep") {
                clusters.getOrPut(keeper.id) { mutableListOf() }.add(photo)
                assigned.add(photo.id)
            } else {
                anchors[photo.id] = photo
                index.add(hash, photo.id)
            }
        }
        clusters.forEach { (id, extras) ->
            result.add(CleanupGroup("Similar photos", anchors[id], extras))
        }
        val keepers = result.mapNotNull { it.keeper?.id }.toSet()
        val blur =
            eligible.filter {
                it.id !in assigned &&
                    it.id !in keepers &&
                    it.decision != "keep" &&
                    it.detail != null &&
                    it.detail < 0.00015 &&
                    (it.clipped ?: 1.0) < 0.4
            }
        if (blur.isNotEmpty()) {
            result.add(CleanupGroup("Very low detail", null, blur))
            assigned.addAll(blur.map { it.id })
        }
        val rejected =
            eligible.filter { it.decision == "reject" && it.id !in assigned && it.id !in keepers }
        if (rejected.isNotEmpty()) result.add(CleanupGroup("Rejected by you", null, rejected))
        return result
    }

    private fun similar(a: Photo, b: Photo): Boolean {
        if (a.sha != null && a.sha == b.sha) return false // Already represented by the exact group.
        if (a.aspect <= 0 || b.aspect <= 0 || abs(a.aspect - b.aspect) / a.aspect > 0.03)
            return false
        val x = a.visual ?: return false
        val y = b.visual ?: return false
        if (x.length != 384 || y.length != 384) return false
        var error = 0.0
        for (i in x.indices step 2) {
            val d =
                (x.substring(i, i + 2).toIntOrNull(16) ?: return false) -
                    (y.substring(i, i + 2).toIntOrNull(16) ?: return false)
            error += d * d
        }
        return error / (192 * 255.0 * 255.0) < 0.009
    }

    fun allowedSelection(groups: List<CleanupGroup>, selected: Set<Long>): List<Photo> {
        val keepers = groups.mapNotNull { it.keeper?.id }.toSet()
        return groups
            .flatMap { it.candidates }
            .distinctBy { it.uri }
            .filter {
                it.id in selected &&
                    it.id !in keepers &&
                    it.decision != "keep" &&
                    it.source == "gallery"
            }
    }
}
