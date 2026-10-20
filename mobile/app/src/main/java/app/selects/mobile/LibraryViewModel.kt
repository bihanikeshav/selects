package app.selects.mobile

import android.app.Application
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class LibraryState(
    val photos: List<Photo> = emptyList(), val processing: Boolean = false,
    val importing: Boolean = false, val message: String? = null,
)

class LibraryViewModel(application: Application) : AndroidViewModel(application) {
    private val store = PhotoStore(application)
    private val work = WorkManager.getInstance(application)
    private val mutableState = MutableStateFlow(LibraryState())
    val state = mutableState.asStateFlow()

    init {
        viewModelScope.launch {
            if (withContext(Dispatchers.IO) { store.nextQueued() != null }) enqueue()
        }
        viewModelScope.launch {
            work.getWorkInfosForUniqueWorkFlow(ANALYSIS_WORK).collect { infos ->
                val photos = withContext(Dispatchers.IO) { store.photos() }
                mutableState.value = mutableState.value.copy(photos = photos,
                    processing = infos.any { it.state in setOf(WorkInfo.State.RUNNING, WorkInfo.State.ENQUEUED) })
            }
        }
    }

    fun import(uris: List<Uri>) {
        if (uris.isEmpty() || mutableState.value.importing) return
        mutableState.value = mutableState.value.copy(importing = true, message = null)
        viewModelScope.launch {
            val failed = withContext(Dispatchers.IO) {
                var failures = 0
                val resolver = getApplication<Application>().contentResolver
                for (uri in uris.distinct()) {
                    try {
                        resolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        val name = resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
                            ?.use { if (it.moveToFirst()) it.getString(0) else null } ?: "Photo"
                        store.add(uri.toString(), name)
                    } catch (_: Exception) { failures++ }
                }
                // Existing failed entries can recover after the picker renews permission.
                store.queueRepairs()
                failures
            }
            enqueue()
            mutableState.value = mutableState.value.copy(importing = false,
                photos = withContext(Dispatchers.IO) { store.photos() },
                message = if (failed > 0) "$failed photos could not be imported. Try selecting local copies." else null)
        }
    }

    fun repair() = viewModelScope.launch {
        val count = withContext(Dispatchers.IO) {
            store.queueRepairs()
            store.photos().count { it.status == "queued" }
        }
        if (count > 0) enqueue()
        mutableState.value = mutableState.value.copy(message =
            if (count == 0) "All photos are up to date." else "Queued $count photos for processing.")
    }

    fun decide(photo: Photo, decision: String) = viewModelScope.launch {
        withContext(Dispatchers.IO) { store.decide(photo.id, decision) }
        mutableState.value = mutableState.value.copy(photos = withContext(Dispatchers.IO) { store.photos() })
    }

    private fun enqueue() {
        // Appending closes the race between the running worker draining its queue and a new import.
        work.enqueueUniqueWork(ANALYSIS_WORK, ExistingWorkPolicy.APPEND_OR_REPLACE,
            OneTimeWorkRequestBuilder<AnalysisWorker>().build())
    }

    override fun onCleared() { store.close() }
}
