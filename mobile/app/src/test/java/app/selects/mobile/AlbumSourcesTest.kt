package app.selects.mobile

import org.junit.Assert.*
import org.junit.Test

class AlbumSourcesTest {
    @Test
    fun aNamedPersonalFolderIsNotExcludedJustBecauseItsParentIsDownloads() {
        assertEquals(AlbumSource.PERSONAL, AlbumSources.classify("My Trip", "Download/My Trip/"))
        assertEquals(AlbumSource.APP_MEDIA, AlbumSources.classify("Download", "Download/"))
    }

    @Test
    fun appSourcesAreExcludedByDefaultButPersonalFoldersAreNot() {
        listOf(
                "WhatsApp Images",
                "WhatsApp Documents",
                "WhatsApp Business Images",
                "Telegram Images",
                "Screenshots",
                "Instagram",
                "Download",
                "ChatGPT",
                "AdobeLightroom",
            )
            .forEach { assertNotEquals(it, AlbumSource.PERSONAL, AlbumSources.classify(it)) }
        listOf(
                "Camera",
                "Expert RAW",
                "Ladakh",
                "My Instagram Holiday",
                "100NIKON",
                "My Screenshots Project",
            )
            .forEach { assertEquals(it, AlbumSource.PERSONAL, AlbumSources.classify(it)) }
    }

    @Test
    fun fullPathsIdentifyHiddenAppFoldersWithoutGuessingFromPhotoContents() {
        assertEquals(
            AlbumSource.MESSAGING,
            AlbumSources.classify(
                "Sent",
                "Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images/Sent/",
            ),
        )
        assertEquals(
            AlbumSource.SOCIAL,
            AlbumSources.classify("Saved", "Pictures/Instagram/Saved/"),
        )
        assertEquals(AlbumSource.PERSONAL, AlbumSources.classify("Holiday", "Pictures/Holiday/"))
    }

    @Test
    fun optingInIsScopedToTheChosenSourceAndDoesNotRemoveTheCatalog() {
        val entries =
            listOf(
                MediaEntry("a", "a", album = "Camera"),
                MediaEntry("b", "b", album = "Screenshots"),
                MediaEntry("c", "c", album = "WhatsApp Images"),
            )
        assertEquals(1, entries.count { AlbumSources.allowed(it, emptySet()) })
        assertEquals(2, entries.count { AlbumSources.allowed(it, setOf(AlbumSource.SCREENSHOTS)) })
        assertEquals(3, entries.size)
    }
}
