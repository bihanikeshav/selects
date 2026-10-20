package app.selects.mobile

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import java.io.File
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class GalleryStoreTest {
    private val context: Context = ApplicationProvider.getApplicationContext()
    private lateinit var store: PhotoStore
    private lateinit var preview: File

    @Before
    fun setup() {
        context.deleteDatabase("selects.db")
        store = PhotoStore(context)
        preview = File(context.filesDir, "preview").apply { writeText("preview") }
    }

    @After
    fun cleanup() {
        store.close()
        preview.delete()
    }

    private fun entry(id: Int, bytes: Long = 100, modified: Long = 1) =
        MediaEntry(
            "content://media/external/images/media/$id",
            "$id.jpg",
            bytes = bytes,
            modified = modified,
        )

    @Test
    fun batchedScanPreservesReviewsAndInvalidatesOnlyChangedFiles() {
        val first = store.upsert(entry(1), "first")
        val second = store.upsert(entry(2), "first")
        store.queueGallery()
        listOf(first, second).forEach {
            store.complete(it, null, Quality(90, .01, 0.0))
            store.saveHash(it, "original")
        }
        val snapshot = store.scanSnapshot()
        store.decide(first, "keep") // A review made after the scan snapshot must survive.
        store.upsertBatch(
            listOf(
                entry(1).copy(metadataRead = true),
                entry(2, modified = 2).copy(metadataRead = true),
                entry(3),
            ),
            "second",
            2,
            snapshot,
        )
        store.finishScan("second")
        assertEquals(3, store.catalog().size)
        assertEquals("keep", store.find(entry(1).uri)!!.decision)
        assertEquals("original", store.find(entry(1).uri)!!.sha)
        assertEquals("ready", store.find(entry(1).uri)!!.status)
        assertNull(store.find(entry(2).uri)!!.sha)
        assertEquals("queued", store.find(entry(2).uri)!!.status)
        assertEquals(2, store.find(entry(1).uri)!!.metadataVersion)
        assertEquals(0, store.find(entry(3).uri)!!.metadataVersion)
    }

    @Test
    fun batchSnapshotIncludesUnavailablePhotosForRestoringAccess() {
        val id = store.upsert(entry(1), "first")
        store.decide(id, "keep")
        store.finishScan("other")
        assertTrue(store.catalog().isEmpty())
        store.upsertBatch(
            listOf(entry(1).copy(metadataRead = true)),
            "restored",
            2,
            store.scanSnapshot(),
        )
        assertEquals(id, store.catalog().single().id)
        assertEquals("keep", store.catalog().single().decision)
    }

    @Test
    fun failedBatchRollsBackItsWholeCheckpoint() {
        try {
            store.upsertBatch(listOf(entry(1), entry(1)), "first", 2, emptyMap())
            fail("Duplicate URI must fail the batch")
        } catch (_: android.database.sqlite.SQLiteConstraintException) {
            assertTrue(store.catalog().isEmpty())
        }
    }

    @Test
    fun scanIsStagedUntilGroupsAreConfirmedAndRepeatScanIsIdempotent() {
        store.upsert(entry(1), "first")
        store.upsert(entry(1), "second")
        assertEquals(1, store.catalog().size)
        assertTrue(store.photos().isEmpty())
        assertNull(store.nextQueued())
        store.confirmGroups(SmartGrouping.suggest(store.catalog()))
        assertEquals(1, store.photos().size)
        assertNotNull(store.nextQueued())
    }

    @Test
    fun changedFilesInvalidateSignaturesButPreserveReviews() {
        val id = store.upsert(entry(1), "first")
        store.queueGallery()
        store.complete(
            id,
            preview.path,
            Quality(90, .01, 0.0),
            VisualSignature("1", "444444".repeat(64), 1.5),
        )
        store.saveHash(id, "original")
        store.decide(id, "keep")
        store.upsert(entry(1, modified = 2), "second")
        val changed = store.catalog().single()
        assertEquals("queued", changed.status)
        assertEquals("keep", changed.decision)
        assertNull(changed.sha)
        assertNull(changed.visualHash)
    }

    @Test
    fun healthyScanAndRepairSkipAnalysisAndKeepHashes() {
        val id = store.upsert(entry(1), "first")
        store.queueGallery()
        store.complete(id, preview.path, Quality(90, .01, 0.0))
        store.saveHash(id, "original")
        store.upsert(entry(1), "second")
        store.queueGallery()
        assertEquals(0, store.queueRepairs())
        assertNull(store.nextQueued())
        assertEquals("original", store.catalog().single().sha)
    }

    @Test
    fun onlySameSizeOriginalsNeedCryptographicHashing() {
        (1..3).forEach { store.upsert(entry(it, bytes = if (it == 3) 200 else 100), "scan") }
        store.queueGallery()
        store.catalog().forEach { store.complete(it.id, preview.path, Quality(90, .01, 0.0)) }
        assertEquals(2, store.hashCandidates().size)
        val first = store.hashCandidates().first()
        store.saveHash(first.id, "done")
        assertEquals(1, store.hashCandidates().size)
    }

    @Test
    fun partialScanHidesUnavailableRowsWithoutLosingTheirReviews() {
        val id = store.upsert(entry(1), "old")
        store.decide(id, "keep")
        store.upsert(entry(2), "new")
        store.finishScan("new")
        assertEquals(1, store.catalog().size)
        store.upsert(entry(1), "restored")
        assertEquals("keep", store.find(entry(1).uri)!!.decision)
    }

    @Test
    fun versionOneMigrationPreservesTheExistingLibrary() {
        store.close()
        context.deleteDatabase("selects.db")
        context.openOrCreateDatabase("selects.db", 0, null).use { db ->
            db.execSQL(
                """CREATE TABLE photos(id INTEGER PRIMARY KEY,uri TEXT UNIQUE,name TEXT,status TEXT,
                preview TEXT,score INTEGER,reason TEXT,decision TEXT,error TEXT,analysis_version INTEGER)"""
            )
            db.execSQL(
                "INSERT INTO photos VALUES(1,'content://old','old.jpg','ready',NULL,90,'detail','keep',NULL,1)"
            )
            db.version = 1
        }
        store = PhotoStore(context)
        assertEquals("keep", store.photos().single().decision)
        assertTrue(store.photos().single().imported)
        assertTrue(store.jobs().isEmpty())
    }
}
