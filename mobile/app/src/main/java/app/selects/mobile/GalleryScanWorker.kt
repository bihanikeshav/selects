package app.selects.mobile

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlin.coroutines.coroutineContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext

const val SCAN_WORK = "selects-gallery-scan"

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class GalleryScanWorker(context: Context, params: WorkerParameters) :
    CoroutineWorker(context, params) {
    override suspend fun doWork(): Result =
        withContext(Dispatchers.IO) {
            PhotoStore(applicationContext).use { store ->
                try {
                    if (!MediaAccess.any(applicationContext)) {
                        store.progress("scan", "Photo access needed", 0, 0, "error")
                        return@withContext Result.failure()
                    }
                    store.progress("scan", "Reading gallery", 0, 0)
                    setProgress(workDataOf("done" to 0))
                    val entries = MediaAccess.images(applicationContext)
                    val seen = id.toString()
                    val version = if (MediaAccess.location(applicationContext)) 2 else 1
                    val previous = store.scanSnapshot()
                    val cleanup = inputData.getBoolean("cleanup", false)
                    val included = MediaAccess.includedSources(applicationContext)
                    val readUris =
                        entries
                            .filter { cleanup || AlbumSources.allowed(it, included) }
                            .map { it.uri }
                            .toHashSet()
                    val metadataDispatcher = Dispatchers.IO.limitedParallelism(4)
                    val deadline = System.currentTimeMillis() + 360_000
                    var lastUpdate = 0L
                    val started = android.os.SystemClock.elapsedRealtime()
                    var lastLog = started
                    var unread = 0
                    var done = 0
                    var inventoried = 0
                    entries.chunked(128).forEach { batch ->
                        coroutineContext.ensureActive()
                        if (System.currentTimeMillis() > deadline) return@withContext Result.retry()
                        val enriched = coroutineScope {
                            batch
                                .map { entry ->
                                    async(metadataDispatcher) {
                                        ensureActive()
                                        MediaAccess.metadata(
                                            applicationContext,
                                            entry,
                                            previous[entry.uri],
                                            locationAllowed = version == 2,
                                            readOriginal = entry.uri in readUris,
                                        )
                                    }
                                }
                                .awaitAll()
                        }
                        coroutineContext.ensureActive()
                        store.upsertBatch(enriched, seen, version, previous)
                        inventoried += batch.size
                        done += batch.count { it.uri in readUris }
                        unread += enriched.count { it.uri in readUris && !it.metadataRead }
                        val elapsed = android.os.SystemClock.elapsedRealtime()
                        if (elapsed - lastLog >= 5_000 || inventoried == entries.size) {
                            android.util.Log.d(
                                "SelectsScan",
                                "photos=$done/${readUris.size} elapsedMs=${elapsed - started} unread=$unread inventory=$inventoried/${entries.size}",
                            )
                            lastLog = elapsed
                        }
                        if (
                            inventoried == entries.size ||
                                System.currentTimeMillis() - lastUpdate > 500
                        ) {
                            store.progress(
                                "scan",
                                if (cleanup) "Reading gallery for cleanup"
                                else "Reading personal photo dates and locations",
                                done,
                                readUris.size,
                            )
                            setProgress(workDataOf("done" to done))
                            lastUpdate = System.currentTimeMillis()
                        }
                    }
                    store.finishScan(seen)
                    store.progress("scan", "Gallery ready", done, readUris.size, "complete")
                    if (inputData.getBoolean("cleanup", false)) {
                        store.queueGallery()
                        store.queueRepairs()
                        WorkManager.getInstance(applicationContext)
                            .enqueueUniqueWork(
                                ANALYSIS_WORK,
                                ExistingWorkPolicy.APPEND_OR_REPLACE,
                                OneTimeWorkRequestBuilder<AnalysisWorker>().build(),
                            )
                        applicationContext
                            .getSharedPreferences("tasks", 0)
                            .edit()
                            .putBoolean("cleanup_after_scan", false)
                            .apply()
                    }
                    Result.success()
                } catch (error: kotlinx.coroutines.CancellationException) {
                    throw error
                } catch (_: Exception) {
                    store.progress(
                        "scan",
                        "Gallery scan interrupted. Check photo access and retry.",
                        0,
                        0,
                        "error",
                    )
                    Result.failure()
                }
            }
        }
}
