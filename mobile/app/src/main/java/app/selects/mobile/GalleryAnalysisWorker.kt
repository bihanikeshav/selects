package app.selects.mobile

import android.content.Context
import android.graphics.ImageDecoder
import android.os.Build
import android.provider.MediaStore
import androidx.core.net.toUri
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import java.security.MessageDigest
import kotlin.coroutines.coroutineContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext

const val ANALYSIS_WORK = "selects-analysis"

class AnalysisWorker(context: Context, params: WorkerParameters) :
    CoroutineWorker(context, params) {
    override suspend fun doWork(): Result =
        withContext(Dispatchers.IO) {
            PhotoStore(applicationContext).use { store ->
                val deadline = System.currentTimeMillis() + 360_000
                val previous = store.jobs().firstOrNull { it.key == "analysis" }
                val base =
                    if (
                        previous?.stage == "Analyzing detail and similarity" &&
                            previous.status != "complete"
                    )
                        previous.done
                    else 0
                val total = base + store.queuedCount()
                var completed = base
                var lastUpdate = 0L
                store.progress("analysis", "Analyzing detail and similarity", completed, total)
                setProgress(workDataOf("done" to 0))
                while (true) {
                    coroutineContext.ensureActive()
                    if (System.currentTimeMillis() > deadline) return@withContext Result.retry()
                    val photo = store.nextQueued() ?: break
                    try {
                        val source =
                            ImageDecoder.createSource(
                                applicationContext.contentResolver,
                                photo.uri.toUri(),
                            )
                        PhotoProcessor.process(store, photo, source)
                    } catch (error: kotlinx.coroutines.CancellationException) {
                        throw error
                    } catch (_: Exception) {
                        store.fail(
                            photo.id,
                            "Could not read this photo. Check access or try reprocessing.",
                        )
                    }
                    completed++
                    if (System.currentTimeMillis() - lastUpdate > 500 || store.queuedCount() == 0) {
                        store.progress(
                            "analysis",
                            "Analyzing detail and similarity",
                            completed,
                            total,
                        )
                        setProgress(workDataOf("done" to completed))
                        lastUpdate = System.currentTimeMillis()
                    }
                }
                // Exact matches require unredacted original bytes. Never call redacted GPS variants
                // byte-identical.
                val canReadOriginal =
                    Build.VERSION.SDK_INT < 29 || MediaAccess.location(applicationContext)
                val candidates = if (canReadOriginal) store.hashCandidates() else emptyList()
                candidates.forEachIndexed { index, photo ->
                    coroutineContext.ensureActive()
                    if (System.currentTimeMillis() > deadline) return@withContext Result.retry()
                    try {
                        val uri =
                            if (Build.VERSION.SDK_INT >= 29)
                                MediaStore.setRequireOriginal(photo.uri.toUri())
                            else photo.uri.toUri()
                        val digest = MessageDigest.getInstance("SHA-256")
                        applicationContext.contentResolver.openInputStream(uri)?.use { stream ->
                            val buffer = ByteArray(64 * 1024)
                            while (true) {
                                coroutineContext.ensureActive()
                                val count = stream.read(buffer)
                                if (count < 0) break
                                digest.update(buffer, 0, count)
                            }
                        } ?: throw java.io.IOException("Unavailable")
                        val current = MediaAccess.selected(applicationContext, photo.uri.toUri())
                        if (current.bytes == photo.bytes && current.modified == photo.modified)
                            store.saveHash(
                                photo.id,
                                digest.digest().joinToString("") { "%02x".format(it) },
                            )
                        else
                            store.upsert(
                                current,
                                "changed-during-analysis",
                                if (MediaAccess.location(applicationContext)) 2 else 1,
                            )
                    } catch (error: kotlinx.coroutines.CancellationException) {
                        throw error
                    } catch (_: Exception) {
                        /* A readable preview remains useful. Retry hashing on the next analysis run. */
                    }
                    if (
                        System.currentTimeMillis() - lastUpdate > 500 ||
                            index == candidates.lastIndex
                    ) {
                        store.progress(
                            "analysis",
                            "Verifying identical files",
                            index + 1,
                            candidates.size,
                        )
                        setProgress(workDataOf("hashes" to index + 1))
                        lastUpdate = System.currentTimeMillis()
                    }
                }
                if (store.queuedCount() > 0) return@withContext Result.retry()
                store.progress("analysis", "Analysis complete", completed, total, "complete")
                Result.success()
            }
        }
}
