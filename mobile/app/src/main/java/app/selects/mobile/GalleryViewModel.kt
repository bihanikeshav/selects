package app.selects.mobile

import android.app.Application
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import androidx.activity.result.IntentSenderRequest
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.workDataOf
import kotlin.coroutines.coroutineContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

data class LibraryState(
    val photos: List<Photo> = emptyList(),
    val catalog: List<Photo> = emptyList(),
    val groups: List<SmartGroup> = emptyList(),
    val cleanup: List<CleanupGroup> = emptyList(),
    val jobs: List<JobProgress> = emptyList(),
    val scanning: Boolean = false,
    val processing: Boolean = false,
    val importing: Boolean = false,
    val fullAccess: Boolean = false,
    val hasAccess: Boolean = false,
    val locationAccess: Boolean = false,
    val includedSources: Set<AlbumSource> = emptySet(),
    val albumPhotos: List<Photo> = emptyList(),
    val sourceCounts: Map<AlbumSource, Int> = emptyMap(),
    val manualSelection: Boolean = false,
    val message: String? = null,
    val pending: Int = 0,
    val scope: String = "Camera & personal folders",
)

class LibraryViewModel(application: Application) : AndroidViewModel(application) {
    private val store = PhotoStore(application)
    private val work = WorkManager.getInstance(application)
    private val mutableState =
        MutableStateFlow(LibraryState(includedSources = MediaAccess.includedSources(application)))
    val state = mutableState.asStateFlow()
    private val refreshLock = Mutex()
    private var proposalKey = ""
    private var scopeIds: Set<Long>? = null
    private var pendingTrash = emptyList<Photo>()
    private var lastCatalogRefresh = 0L

    init {
        listOf(SCAN_WORK, ANALYSIS_WORK).forEach { key ->
            viewModelScope.launch {
                work.getWorkInfosForUniqueWorkFlow(key).collect { infos ->
                    val active =
                        infos.any {
                            it.state in
                                setOf(
                                    WorkInfo.State.RUNNING,
                                    WorkInfo.State.ENQUEUED,
                                    WorkInfo.State.BLOCKED,
                                )
                        }
                    mutableState.value =
                        if (key == SCAN_WORK) mutableState.value.copy(scanning = active)
                        else mutableState.value.copy(processing = active)
                    refresh(progressOnly = active)
                }
            }
        }
        viewModelScope.launch { refresh() }
    }

    suspend fun refresh(progressOnly: Boolean = false) =
        refreshLock.withLock {
            val now = android.os.SystemClock.elapsedRealtime()
            if (progressOnly && lastCatalogRefresh > 0 && now - lastCatalogRefresh < 10_000) {
                val jobs = withContext(Dispatchers.IO) { store.jobs() }
                mutableState.value = mutableState.value.copy(jobs = jobs)
                return@withLock
            }
            lastCatalogRefresh = now
            val app = getApplication<Application>()
            val snapshot =
                withContext(Dispatchers.IO) {
                    Triple(store.photos(), store.catalog(), store.jobs())
                }
            val current = mutableState.value
            val eligibility =
                withContext(Dispatchers.Default) {
                    val gallery = snapshot.second.filter { it.source == "gallery" }
                    gallery.filter { AlbumSources.allowed(it, current.includedSources) } to
                        gallery.groupingBy(AlbumSources::classify).eachCount()
                }
            val albumPhotos = eligibility.first
            val pending = snapshot.second.count { it.status == "queued" }
            val candidates =
                (if (scopeIds == null) albumPhotos
                    else snapshot.second.filter { it.id in scopeIds!! })
                    .filter { !it.imported }
            val key =
                candidates
                    .map {
                        listOf(
                            it.id,
                            it.taken,
                            it.album,
                            it.albumId,
                            it.relativePath,
                            it.latitude,
                            it.longitude,
                        )
                    }
                    .hashCode()
                    .toString()
            val groups =
                if (key == proposalKey) current.groups
                else withContext(Dispatchers.Default) { SmartGrouping.suggest(candidates) }
            proposalKey = key
            val cleanup =
                if (current.scanning || current.processing || pending > 0) emptyList()
                else withContext(Dispatchers.Default) { Cleanup.suggestions(snapshot.second) }
            mutableState.value =
                mutableState.value.copy(
                    photos = snapshot.first,
                    catalog = snapshot.second,
                    albumPhotos = albumPhotos,
                    sourceCounts = eligibility.second,
                    groups = groups,
                    cleanup = cleanup,
                    jobs = snapshot.third,
                    pending = pending,
                    fullAccess = MediaAccess.full(app),
                    hasAccess = MediaAccess.any(app),
                    locationAccess = MediaAccess.location(app),
                )
        }

    fun onResume() {
        viewModelScope.launch { refresh() }
    }

    fun message(text: String) {
        mutableState.value = mutableState.value.copy(message = text)
    }

    fun scan(cleanup: Boolean = false) {
        if (!MediaAccess.any(getApplication())) {
            message("Allow photo access to scan the gallery.")
            return
        }
        getApplication<Application>()
            .getSharedPreferences("tasks", 0)
            .edit()
            .putBoolean("cleanup_after_scan", cleanup)
            .apply()
        scopeIds = null
        mutableState.value =
            mutableState.value.copy(
                scope = "Camera & personal folders",
                message = null,
                scanning = true,
                manualSelection = false,
            )
        work.enqueueUniqueWork(
            SCAN_WORK,
            ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<GalleryScanWorker>()
                .setInputData(workDataOf("cleanup" to cleanup))
                .build(),
        )
    }

    fun allGroups() {
        scopeIds = null
        proposalKey = ""
        mutableState.value =
            mutableState.value.copy(
                scope = "Camera & personal folders",
                manualSelection = false,
                groups = emptyList(),
            )
        viewModelScope.launch { refresh() }
    }

    fun nativeSelection(uris: List<Uri>) {
        if (uris.isEmpty()) return
        mutableState.value = mutableState.value.copy(importing = true, message = null)
        viewModelScope.launch {
            val ids = mutableSetOf<Long>()
            var failures = 0
            withContext(Dispatchers.IO) {
                val app = getApplication<Application>()
                val resolver = app.contentResolver
                uris.distinct().forEach { uri ->
                    coroutineContext.ensureActive()
                    try {
                        try {
                            resolver.takePersistableUriPermission(
                                uri,
                                Intent.FLAG_GRANT_READ_URI_PERMISSION,
                            )
                        } catch (_: SecurityException) {}
                        if (
                            !MediaAccess.direct(uri) &&
                                resolver.persistedUriPermissions.none {
                                    it.uri == uri && it.isReadPermission
                                }
                        )
                            throw SecurityException("Nonpersistent gallery reference")
                        val entry = MediaAccess.selected(app, uri)
                        val enriched = MediaAccess.metadata(app, entry, store.find(entry.uri))
                        ids.add(
                            store.upsert(
                                enriched,
                                "native-selection",
                                if (enriched.metadataRead) {
                                    if (MediaAccess.location(app)) 2 else 1
                                } else 0,
                            )
                        )
                    } catch (error: kotlinx.coroutines.CancellationException) {
                        throw error
                    } catch (_: Exception) {
                        failures++
                    }
                }
            }
            scopeIds = ids
            mutableState.value =
                mutableState.value.copy(
                    importing = false,
                    groups = emptyList(),
                    scope = "Gallery selection",
                    manualSelection = true,
                    message =
                        if (failures > 0)
                            "$failures photos could not retain access. Use the album browser instead."
                        else "Review the suggested split before adding these photos.",
                )
            refresh()
        }
    }

    fun includeSource(source: AlbumSource, included: Boolean) {
        val sources =
            mutableState.value.includedSources.let { if (included) it + source else it - source }
        getApplication<Application>()
            .getSharedPreferences("gallery_filters", 0)
            .edit()
            .putStringSet("included_sources", sources.map { it.name }.toSet())
            .apply()
        scopeIds = null
        mutableState.value =
            mutableState.value.copy(
                includedSources = sources,
                groups = emptyList(),
                manualSelection = false,
                scope = "Camera & personal folders",
            )
        proposalKey = ""
        viewModelScope.launch { refresh() }
    }

    fun reviewAlbums(keys: Set<String>) {
        scopeIds =
            mutableState.value.catalog
                .filter { it.albumId.ifBlank { it.album } in keys }
                .map { it.id }
                .toSet()
        mutableState.value =
            mutableState.value.copy(
                manualSelection = true,
                scope = "${keys.size} selected folders",
                groups = emptyList(),
            )
        proposalKey = ""
        viewModelScope.launch { refresh() }
    }

    fun merge(ids: Set<Long>) {
        val selected = mutableState.value.groups.filter { it.id in ids }
        if (selected.size < 2) return
        val merged =
            SmartGroup(
                selected.first().id,
                selected.first().name,
                "Combined by you",
                selected.flatMap { it.photos }.sortedBy { it.taken },
            )
        mutableState.value =
            mutableState.value.copy(
                groups = mutableState.value.groups.filter { it.id !in ids } + merged
            )
    }

    fun rename(id: Long, name: String) {
        mutableState.value =
            mutableState.value.copy(
                groups =
                    mutableState.value.groups.map {
                        if (it.id == id) it.copy(name = name.take(120)) else it
                    }
            )
    }

    fun confirm(ids: Set<Long>) =
        viewModelScope.launch {
            val groups = mutableState.value.groups.filter { it.id in ids }
            if (groups.isEmpty()) return@launch
            withContext(Dispatchers.IO) { store.confirmGroups(groups) }
            enqueueAnalysis()
            message(
                "Added ${groups.sumOf { it.photos.size }} photo references. Originals stay in place."
            )
            refresh()
        }

    fun cleanupScan() =
        viewModelScope.launch {
            if (mutableState.value.scanning) {
                message("Let the gallery scan finish first.")
                return@launch
            }
            if (!MediaAccess.any(getApplication())) {
                message("Allow photo access and scan the gallery first.")
                return@launch
            }
            scan(cleanup = true)
        }

    fun repair() =
        viewModelScope.launch {
            val count =
                withContext(Dispatchers.IO) {
                    store.queueRepairs()
                    store.queuedCount()
                }
            if (count > 0) enqueueAnalysis()
            message(
                if (count == 0) "Photos are up to date." else "Queued $count photos for processing."
            )
            refresh()
        }

    fun decide(photo: Photo, decision: String) =
        viewModelScope.launch {
            withContext(Dispatchers.IO) { store.decide(photo.id, decision) }
            refresh()
        }

    fun pause(key: String) {
        work.cancelUniqueWork(if (key == "scan") SCAN_WORK else ANALYSIS_WORK)
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                store
                    .jobs()
                    .firstOrNull { it.key == key }
                    ?.let { store.progress(key, it.stage, it.done, it.total, "paused") }
            }
            refresh()
        }
    }

    fun resume(key: String) {
        if (key == "scan")
            scan(
                getApplication<Application>()
                    .getSharedPreferences("tasks", 0)
                    .getBoolean("cleanup_after_scan", false)
            )
        else enqueueAnalysis()
    }

    private fun enqueueAnalysis() {
        mutableState.value = mutableState.value.copy(processing = true)
        work.enqueueUniqueWork(
            ANALYSIS_WORK,
            ExistingWorkPolicy.APPEND_OR_REPLACE,
            OneTimeWorkRequestBuilder<AnalysisWorker>().build(),
        )
    }

    suspend fun trashRequest(selected: Set<Long>): IntentSenderRequest? {
        if (Build.VERSION.SDK_INT < 30) {
            message("Gallery trash requires Android 11 or later.")
            return null
        }
        if (mutableState.value.scanning || mutableState.value.processing) return null
        val current = withContext(Dispatchers.IO) { store.catalog() }
        val groups = withContext(Dispatchers.Default) { Cleanup.suggestions(current) }
        val batch =
            Cleanup.allowedSelection(groups, selected)
                .filter { MediaAccess.direct(Uri.parse(it.uri)) }
                .take(200)
        if (batch.isEmpty()) {
            message(
                "No eligible photos selected. Kept photos and group representatives are protected."
            )
            return null
        }
        return try {
            val intent =
                MediaStore.createTrashRequest(
                    getApplication<Application>().contentResolver,
                    batch.map { Uri.parse(it.uri) },
                    true,
                )
            pendingTrash = batch
            IntentSenderRequest.Builder(intent.intentSender).build()
        } catch (_: Exception) {
            message("Android could not prepare trash. Check gallery access and retry.")
            null
        }
    }

    fun trashResult(approved: Boolean) =
        viewModelScope.launch {
            val batch = pendingTrash
            pendingTrash = emptyList()
            if (!approved) {
                message("Canceled. No photos were moved by Selects.")
                return@launch
            }
            val verified =
                withContext(Dispatchers.IO) {
                    val moved =
                        batch.filter { photo ->
                            MediaAccess.isTrashed(getApplication(), Uri.parse(photo.uri))
                        }
                    store.markUnavailable(moved.map { it.id })
                    moved.size
                }
            message(
                "Verified $verified of ${batch.size} photos moved to trash. Unverified items stay listed. Restore trashed photos in your gallery if needed."
            )
            refresh()
        }
}
