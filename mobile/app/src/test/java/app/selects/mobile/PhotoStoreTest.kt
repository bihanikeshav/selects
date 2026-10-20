package app.selects.mobile

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class PhotoStoreTest {
    private lateinit var store: PhotoStore
    private lateinit var preview: File

    @Before fun setUp() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        context.deleteDatabase("selects.db")
        store = PhotoStore(context)
        preview = File(context.filesDir, "test-preview.jpg").apply { writeText("preview") }
    }

    @After fun tearDown() { store.close(); preview.delete() }

    @Test fun repeatImportIsIdempotentAndHealthyRepairIsNoOp() {
        assertTrue(store.add("content://photos/1", "日本.jpg"))
        assertFalse(store.add("content://photos/1", "日本.jpg"))
        val photo = store.nextQueued()!!
        store.complete(photo.id, preview.path, Quality(80, .1, .01))
        store.decide(photo.id, "reject")
        assertEquals(0, store.queueRepairs())
        assertNull(store.nextQueued())
        assertEquals("reject", store.photos().single().decision)
    }

    @Test fun failuresAndMissingPreviewsRepairWithoutLosingDecisions() {
        store.add("content://photos/1", "photo.jpg")
        val id = store.nextQueued()!!.id
        store.decide(id, "keep")
        store.fail(id, "Permission expired")
        assertEquals(1, store.queueRepairs())
        assertEquals(0, store.queueRepairs())
        store.complete(id, preview.path, Quality(80, .1, .01))
        preview.delete()
        assertEquals(1, store.queueRepairs())
        assertEquals("keep", store.nextQueued()!!.decision)
    }

    @Test fun staleAnalysisIsQueuedOnce() {
        store.add("content://photos/1", "photo.jpg")
        val id = store.nextQueued()!!.id
        store.complete(id, preview.path, Quality(80, .1, .01))
        store.writableDatabase.execSQL("UPDATE photos SET analysis_version=0")
        assertEquals(1, store.queueRepairs())
        assertEquals(0, store.queueRepairs())
    }

    @Test fun reviewsAndPendingWorkSurviveReopeningTheDatabase() {
        store.add("content://photos/1", "photo.jpg")
        val id = store.nextQueued()!!.id
        store.decide(id, "keep")
        store.close()
        store = PhotoStore(ApplicationProvider.getApplicationContext())
        assertEquals(id, store.nextQueued()!!.id)
        assertEquals("keep", store.photos().single().decision)
    }
}
