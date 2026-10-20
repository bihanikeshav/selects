package app.selects.mobile

import android.content.Context
import android.graphics.ImageDecoder
import androidx.core.net.toUri
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlin.coroutines.coroutineContext

const val ANALYSIS_WORK = "selects-analysis"

class AnalysisWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        PhotoStore(applicationContext).use { store ->
            var completed = 0
            while (true) {
                coroutineContext.ensureActive()
                val photo = store.nextQueued() ?: break
                try {
                    val source = ImageDecoder.createSource(applicationContext.contentResolver, photo.uri.toUri())
                    PhotoProcessor.process(applicationContext, store, photo, source)
                } catch (error: kotlinx.coroutines.CancellationException) {
                    throw error
                } catch (error: Exception) {
                    store.fail(photo.id, when (error) {
                        is SecurityException -> "Photo access expired. Select this photo again."
                        else -> error.message ?: "This photo could not be read. Try importing a JPEG or HEIC."
                    })
                }
                setProgress(workDataOf("completed" to ++completed))
            }
        }
        Result.success()
    }
}
