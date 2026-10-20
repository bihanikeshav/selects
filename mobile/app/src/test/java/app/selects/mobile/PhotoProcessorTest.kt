package app.selects.mobile

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.net.Uri
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File
import java.nio.ByteBuffer

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class PhotoProcessorTest {
    @Test fun decodesAndAnalyzesARealImageWithoutChangingTheOriginal() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        context.deleteDatabase("selects.db")
        val source = File(context.filesDir, "日本旅行.jpg")
        val bitmap = Bitmap.createBitmap(1280, 640, Bitmap.Config.ARGB_8888)
        bitmap.eraseColor(0xff789abc.toInt())
        source.outputStream().use { bitmap.compress(Bitmap.CompressFormat.JPEG, 95, it) }
        bitmap.recycle()
        val original = source.readBytes()
        try {
            PhotoStore(context).use { store ->
                store.add(Uri.fromFile(source).toString(), source.name)
                val photo = store.nextQueued()!!
                store.decide(photo.id, "keep")
                // Native host graphics supports buffer sources; Android resolves picker URIs in the worker.
                PhotoProcessor.process(context, store, photo, ImageDecoder.createSource(ByteBuffer.wrap(original)))
                val result = store.photos().single()
                assertEquals("ready", result.status)
                assertEquals("keep", result.decision)
                assertNotNull(result.score)
                val preview = BitmapFactory.decodeFile(result.preview)
                assertEquals(640, preview.width)
                assertEquals(320, preview.height)
                preview.recycle()
                assertEquals(0, store.queueRepairs())
                assertNull(store.nextQueued())
                assertArrayEquals(original, source.readBytes())
                assertFalse(File(context.filesDir, "previews/${photo.id}.tmp").exists())
            }
        } finally { source.delete() }
    }
}
