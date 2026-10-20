package app.selects.mobile

import java.util.Locale

enum class AlbumSource(val label: String) {
    PERSONAL("Camera & personal folders"),
    SCREENSHOTS("Screenshots"),
    MESSAGING("WhatsApp & messaging"),
    SOCIAL("Social apps"),
    APP_MEDIA("Downloads & app media"),
}

/** Filter album suggestions, never the cleanup inventory or an explicit user selection. */
object AlbumSources {
    private val screenshots =
        setOf("screenshots", "screen shots", "screencaptures", "videocaptures", "screen recordings")
    private val messaging =
        setOf("whatsapp", "whatsapp business", "telegram", "signal", "viber", "messenger", "wechat")
    private val social =
        setOf(
            "instagram",
            "facebook",
            "twitter",
            "x",
            "linkedin",
            "snapchat",
            "pinterest",
            "reddit",
        )
    private val appMedia =
        setOf(
            "download",
            "downloads",
            "adobelightroom",
            "lightroom",
            "snapseed",
            "chatgpt",
            "sketch to image",
            "quick share",
            "samsung flow",
            "gopro-exports",
        )
    private val cameras =
        setOf(
            "camera",
            "expert raw",
            "raw",
            "dcim",
            "gallery",
            "pictures",
            "selected photos",
            "photos",
        )
    private val cameraFolder = Regex("\\d{3}[a-z0-9_]{2,}")

    private fun normalize(value: String) = value.trim().lowercase(Locale.ROOT).replace('_', ' ')

    fun classify(photo: Photo): AlbumSource = classify(photo.album, photo.relativePath)

    fun classify(entry: MediaEntry): AlbumSource = classify(entry.album, entry.relativePath)

    fun classify(album: String, relativePath: String = ""): AlbumSource {
        val names = (relativePath.split('/') + album).map(::normalize)
        if (names.any { it in screenshots }) return AlbumSource.SCREENSHOTS
        if (names.any { n -> messaging.any { n == it || n.startsWith("$it ") || n == "com.$it" } })
            return AlbumSource.MESSAGING
        if (names.any { it in social }) return AlbumSource.SOCIAL
        // A named folder inside Downloads can be deliberate. Generic parent directories
        // do not override that folder; known app-owned ancestors still do.
        val leaf = relativePath.trimEnd('/').substringAfterLast('/').let(::normalize)
        if (normalize(album) in appMedia || leaf in appMedia || names.any { it == "android" })
            return AlbumSource.APP_MEDIA
        return AlbumSource.PERSONAL
    }

    fun allowed(photo: Photo, included: Set<AlbumSource>) =
        classify(photo).let { it == AlbumSource.PERSONAL || it in included }

    fun allowed(entry: MediaEntry, included: Set<AlbumSource>) =
        classify(entry).let { it == AlbumSource.PERSONAL || it in included }

    fun camera(photo: Photo): Boolean {
        val name = normalize(photo.album)
        return classify(photo) == AlbumSource.PERSONAL &&
            (name in cameras || cameraFolder.matches(name) || name.isBlank())
    }
}
