package app.selects.mobile

import android.Manifest
import android.app.Activity
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            val scheme =
                if (isSystemInDarkTheme())
                    darkColorScheme(
                        primary = Color(0xFFD1C6AA),
                        background = Color(0xFF181815),
                        surface = Color(0xFF181815),
                    )
                else
                    lightColorScheme(
                        primary = Color(0xFF655B47),
                        background = Color(0xFFFAF9F4),
                        surface = Color(0xFFFAF9F4),
                    )
            MaterialTheme(colorScheme = scheme) { LibraryScreen() }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LibraryScreen(model: LibraryViewModel = viewModel()) {
    val state by model.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var page by rememberSaveable { mutableIntStateOf(1) }
    var reviewingId by rememberSaveable { mutableStateOf<Long?>(null) }
    var pendingAction by rememberSaveable { mutableStateOf("scan") }
    val reviewing = state.catalog.firstOrNull { it.id == reviewingId }
    val lifecycle = LocalLifecycleOwner.current
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) model.onResume()
        }
        lifecycle.lifecycle.addObserver(observer)
        onDispose { lifecycle.lifecycle.removeObserver(observer) }
    }
    BackHandler(reviewing != null) { reviewingId = null }
    val gallery =
        rememberLauncherForActivityResult(NativeGalleryContract()) { uris ->
            model.nativeSelection(uris)
            page = 1
        }
    fun action(mode: String) {
        when (mode) {
            "gallery" ->
                try {
                    gallery.launch(Unit)
                } catch (_: Exception) {
                    model.message("No gallery app could open. Use Choose albums instead.")
                }
            "cleanup" -> model.cleanupScan()
            else -> model.scan()
        }
    }
    val permission =
        rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
            model.onResume()
            if (MediaAccess.any(context)) action(pendingAction)
            else model.message("Photo access was not granted. Your gallery is unchanged.")
        }
    val location =
        rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
            model.onResume()
            if (MediaAccess.any(context)) model.scan()
        }
    val request: (String) -> Unit = { mode ->
        if (MediaAccess.any(context)) action(mode)
        else {
            pendingAction = mode
            permission.launch(MediaAccess.permissions())
        }
    }
    val trash =
        rememberLauncherForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) {
            model.trashResult(it.resultCode == Activity.RESULT_OK)
        }
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(if (reviewing == null) "Selects" else "Review photo") },
                navigationIcon = {
                    if (reviewing != null)
                        TextButton(onClick = { reviewingId = null }) { Text("Back") }
                },
                actions = {
                    if (reviewing == null)
                        TextButton(
                            onClick = { model.repair() },
                            enabled = !state.scanning && !state.processing,
                        ) {
                            Text("Reprocess")
                        }
                },
            )
        }
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            if (reviewing == null)
                PrimaryTabRow(selectedTabIndex = page) {
                    listOf("Library", "Add photos", "Cleanup").forEachIndexed { i, title ->
                        Tab(selected = page == i, onClick = { page = i }, text = { Text(title) })
                    }
                }
            state.message
                ?.takeIf { it.isNotBlank() }
                ?.let { message ->
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            message,
                            Modifier.weight(1f),
                            style = MaterialTheme.typography.bodySmall,
                        )
                        TextButton(onClick = { model.message("") }) { Text("Dismiss") }
                    }
                }
            Column(Modifier.padding(horizontal = 16.dp)) { ProgressPanels(state, model) }
            Box(Modifier.weight(1f).padding(horizontal = 16.dp)) {
                if (reviewing != null) ReviewPhoto(reviewing, model::decide)
                else
                    when (page) {
                        0 -> LibraryPage(state, model) { reviewingId = it.id }
                        1 ->
                            ImportPage(
                                state,
                                model,
                                request,
                                { permission.launch(MediaAccess.permissions()) },
                                {
                                    if (Build.VERSION.SDK_INT >= 29)
                                        location.launch(Manifest.permission.ACCESS_MEDIA_LOCATION)
                                },
                            ) { ids ->
                                model.confirm(ids)
                                page = 0
                            }
                        2 ->
                            CleanupPage(
                                state,
                                model,
                                { request("cleanup") },
                                { photo -> reviewingId = photo.id },
                            ) { selected ->
                                scope.launch {
                                    model.trashRequest(selected)?.let { trash.launch(it) }
                                }
                            }
                    }
            }
        }
    }
}

@Composable
private fun ProgressPanels(state: LibraryState, model: LibraryViewModel) {
    state.jobs.forEach { job ->
        val active = if (job.key == "scan") state.scanning else state.processing
        if (active || job.status != "complete")
            Column(Modifier.padding(top = 8.dp, bottom = 4.dp)) {
                Text(job.stage, style = MaterialTheme.typography.labelLarge)
                if (job.total > 0) {
                    LinearProgressIndicator(
                        progress = { job.fraction },
                        modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                    )
                    Text(
                        "${job.done} of ${job.total} · ${(job.fraction*100).toInt()}%",
                        style = MaterialTheme.typography.bodySmall,
                    )
                } else if (active)
                    LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 6.dp))
                TextButton(
                    onClick = { if (active) model.pause(job.key) else model.resume(job.key) }
                ) {
                    Text(if (active) "Pause" else if (job.status == "error") "Retry" else "Resume")
                }
            }
    }
    if (
        (state.scanning || state.processing || state.importing) &&
            state.jobs.none { it.status == "running" }
    ) {
        LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 8.dp))
        Text(
            if (state.importing) "Reading your selection" else "Starting background work",
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
private fun ImportPage(
    state: LibraryState,
    model: LibraryViewModel,
    request: (String) -> Unit,
    changeAccess: () -> Unit,
    allowLocation: () -> Unit,
    confirm: (Set<Long>) -> Unit,
) {
    var albumsOpen by rememberSaveable { mutableStateOf(false) }
    var selectedAlbums by remember { mutableStateOf(emptySet<String>()) }
    var selectedGroups by remember { mutableStateOf(emptySet<Long>()) }
    var editing by remember { mutableStateOf<Long?>(null) }
    val busy = state.scanning || state.processing || state.importing
    val albums =
        remember(state.catalog) {
            state.catalog
                .groupBy { it.albumId.ifBlank { it.album } }
                .entries
                .sortedBy { it.value.first().album }
        }
    val groups =
        state.groups.filter { g ->
            selectedAlbums.isEmpty() ||
                g.photos.any { it.albumId.ifBlank { it.album } in selectedAlbums }
        }
    LaunchedEffect(state.groups.map { it.photos.map(Photo::id) }) { selectedGroups = emptySet() }
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(top = 20.dp, bottom = 32.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Text("Your gallery, organized", style = MaterialTheme.typography.headlineMedium)
            Text(
                "Choose individual photos, whole albums, or scan the phone. Confirm the suggested split before adding anything.",
                Modifier.padding(top = 8.dp),
            )
            Text(
                "Originals stay in your gallery. Selects saves references and analysis, not photo copies.",
                Modifier.padding(top = 8.dp),
                style = MaterialTheme.typography.bodySmall,
            )
        }
        item {
            Button(
                onClick = {
                    selectedAlbums = emptySet()
                    albumsOpen = false
                    request("scan")
                },
                enabled = !busy,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Scan phone gallery")
            }
        }
        item {
            OutlinedButton(
                onClick = {
                    model.allGroups()
                    albumsOpen = true
                    if (state.catalog.isEmpty()) request("scan")
                },
                enabled = !busy,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Choose whole albums")
            }
        }
        item {
            OutlinedButton(
                onClick = {
                    albumsOpen = false
                    selectedAlbums = emptySet()
                    request("gallery")
                },
                enabled = !busy,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Select in native Gallery")
            }
        }
        if (!state.fullAccess)
            item {
                Text(
                    if (state.hasAccess)
                        "Limited access: only photos allowed by Android are visible. Allow all photos for whole-phone organization and cleanup."
                    else "Photo-library access is needed to browse albums and scan the phone.",
                    style = MaterialTheme.typography.bodySmall,
                )
                TextButton(onClick = changeAccess, enabled = !busy) {
                    Text(if (state.hasAccess) "Change photo access" else "Allow photo access")
                }
            }
        if (Build.VERSION.SDK_INT >= 29 && !state.locationAccess)
            item {
                Text(
                    "Photo locations are optional. Allow embedded location metadata for location-based groups and identical-original checks. No live-location access.",
                    style = MaterialTheme.typography.bodySmall,
                )
                TextButton(onClick = allowLocation, enabled = !busy && state.hasAccess) {
                    Text("Use photo locations")
                }
            }
        if (albumsOpen) {
            item { Text("Albums", style = MaterialTheme.typography.titleLarge) }
            items(albums, key = { it.key }) { album ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(
                        checked = album.key in selectedAlbums,
                        onCheckedChange = { checked ->
                            selectedAlbums =
                                if (checked) selectedAlbums + album.key
                                else selectedAlbums - album.key
                        },
                    )
                    Column {
                        Text(album.value.first().album.ifBlank { "Selected photos" })
                        Text(
                            "${album.value.size} photos",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
            }
            item {
                Button(
                    onClick = { albumsOpen = false },
                    enabled = selectedAlbums.isNotEmpty() && !busy,
                ) {
                    Text("Review split for selected albums")
                }
            }
        } else {
            item {
                Text(
                    "${state.catalog.size} accessible photos",
                    style = MaterialTheme.typography.titleMedium,
                )
                Text(
                    "${groups.size} suggested groups · ${state.scope}",
                    style = MaterialTheme.typography.bodySmall,
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Split at every day", Modifier.weight(1f))
                    Switch(
                        checked = state.daily,
                        onCheckedChange = model::splitDaily,
                        enabled = !busy,
                    )
                }
                Text(
                    "Dates use capture time when available, otherwise file dates. GPS is used only when present. Review the split below.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            if (groups.isNotEmpty())
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(
                            onClick = {
                                selectedGroups =
                                    if (selectedGroups.size == groups.size) emptySet()
                                    else groups.map { it.id }.toSet()
                            },
                            enabled = !busy,
                        ) {
                            Text("Select all groups")
                        }
                        TextButton(
                            onClick = { model.merge(selectedGroups) },
                            enabled = selectedGroups.size >= 2 && !busy,
                        ) {
                            Text("Merge selected")
                        }
                    }
                }
            items(groups, key = { it.id }) { group ->
                Column {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(
                            checked = group.id in selectedGroups,
                            onCheckedChange = { checked ->
                                selectedGroups =
                                    if (checked) selectedGroups + group.id
                                    else selectedGroups - group.id
                            },
                            enabled = !busy,
                        )
                        Column(Modifier.weight(1f)) {
                            Text(group.name, style = MaterialTheme.typography.titleSmall)
                            Text(
                                "${group.photos.size} photos · ${group.reason}",
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                    if (editing == group.id)
                        OutlinedTextField(
                            value = group.name,
                            onValueChange = { model.rename(group.id, it) },
                            label = { Text("Group name") },
                            modifier = Modifier.fillMaxWidth(),
                        )
                    TextButton(
                        onClick = { editing = if (editing == group.id) null else group.id },
                        enabled = !busy,
                    ) {
                        Text(if (editing == group.id) "Done naming" else "Rename group")
                    }
                    HorizontalDivider()
                }
            }
            if (groups.isNotEmpty())
                item {
                    Button(
                        onClick = { confirm(selectedGroups) },
                        enabled = selectedGroups.isNotEmpty() && !busy,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Confirm ${selectedGroups.size} groups")
                    }
                }
        }
    }
}

@Composable
private fun LibraryPage(state: LibraryState, model: LibraryViewModel, open: (Photo) -> Unit) {
    var filter by rememberSaveable { mutableStateOf("All") }
    var collection by rememberSaveable { mutableStateOf<String?>(null) }
    val collections = state.photos.map { it.collection }.filter { it.isNotBlank() }.distinct()
    Column(Modifier.fillMaxSize().padding(top = 16.dp)) {
        Text("Your photos", style = MaterialTheme.typography.headlineMedium)
        Text(
            "${state.photos.size} added · ${state.photos.count { it.decision=="keep" }} kept",
            style = MaterialTheme.typography.bodyMedium,
        )
        LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            item {
                FilterChip(
                    selected = collection == null,
                    onClick = { collection = null },
                    label = { Text("All groups") },
                )
            }
            items(collections) { name ->
                FilterChip(
                    selected = collection == name,
                    onClick = { collection = name },
                    label = { Text(name) },
                )
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("All", "Picks", "Kept").forEach { label ->
                FilterChip(
                    selected = filter == label,
                    onClick = { filter = label },
                    label = { Text(label) },
                )
            }
        }
        val visible =
            state.photos.filter { p ->
                (collection == null || p.collection == collection) &&
                    when (filter) {
                        "Picks" -> p.status == "ready" && p.decision != "reject"
                        "Kept" -> p.decision == "keep"
                        else -> true
                    }
            }
        if (visible.isEmpty())
            Text(
                "Add photos from the Add photos tab, then confirm their groups.",
                Modifier.padding(vertical = 24.dp),
            )
        LazyVerticalGrid(
            GridCells.Adaptive(160.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            contentPadding = PaddingValues(bottom = 24.dp),
        ) {
            items(visible, key = { it.id }) { p -> PhotoTile(p, model::decide) { open(p) } }
        }
    }
}

@Composable
private fun CleanupPage(
    state: LibraryState,
    model: LibraryViewModel,
    scan: () -> Unit,
    open: (Photo) -> Unit,
    trash: (Set<Long>) -> Unit,
) {
    var selected by remember { mutableStateOf(emptySet<Long>()) }
    val allowed = Cleanup.allowedSelection(state.cleanup, selected)
    val busy = state.scanning || state.processing || state.importing
    LaunchedEffect(state.cleanup) {
        selected = selected.intersect(state.cleanup.flatMap { it.candidates }.map { it.id }.toSet())
    }
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(top = 20.dp, bottom = 32.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Text("Make room for keepers", style = MaterialTheme.typography.headlineMedium)
            Text(
                "Find unwanted photos across the accessible phone gallery. Nothing moves to trash until you choose photos and confirm with Android.",
                Modifier.padding(top = 8.dp),
            )
            Button(
                onClick = scan,
                enabled = !busy,
                modifier = Modifier.fillMaxWidth().padding(top = 16.dp),
            ) {
                Text("Scan phone for cleanup")
            }
            if (!state.fullAccess)
                Text(
                    "Whole-phone cleanup needs access to all photos. Limited access scans only Android's allowed selection.",
                    style = MaterialTheme.typography.bodySmall,
                )
            if (Build.VERSION.SDK_INT >= 29 && !state.locationAccess)
                Text(
                    "Identical-original checking needs photo-location metadata access. Enable it in Add photos. Similarity and detail checks still work.",
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 8.dp),
                )
        }
        if (state.pending > 0 && !state.processing)
            item {
                Text("${state.pending} photos are waiting for analysis.")
                TextButton(onClick = { model.resume("analysis") }) { Text("Resume analysis") }
            }
        if (!busy && state.pending == 0 && state.cleanup.isEmpty())
            item {
                Text(
                    "No cleanup suggestions yet. Run a gallery scan, then review the results here."
                )
            }
        if (state.cleanup.isNotEmpty())
            item {
                Text(
                    "Review suggestions. Similar photos and low-detail images may be intentional.",
                    style = MaterialTheme.typography.bodySmall,
                )
                TextButton(
                    onClick = {
                        selected =
                            if (selected.isEmpty())
                                state.cleanup.flatMap { it.candidates }.map { it.id }.toSet()
                            else emptySet()
                    }
                ) {
                    Text(if (selected.isEmpty()) "Select suggested extras" else "Clear selection")
                }
                Text(
                    "${allowed.size} selected · ${formatBytes(allowed.sumOf { it.bytes })} estimated",
                    style = MaterialTheme.typography.titleMedium,
                )
                Button(
                    onClick = { trash(selected) },
                    enabled = allowed.isNotEmpty() && !busy && Build.VERSION.SDK_INT >= 30,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("Review trash request (${allowed.size.coerceAtMost(200)})")
                }
                if (allowed.size > 200)
                    Text(
                        "Android will confirm the first 200. Review another batch afterward.",
                        style = MaterialTheme.typography.bodySmall,
                    )
            }
        state.cleanup.forEachIndexed { index, group ->
            item(key = "heading-$index") {
                Text(
                    "${group.kind} · ${group.candidates.size} suggestions",
                    style = MaterialTheme.typography.titleLarge,
                )
                group.keeper?.let { keeper ->
                    Text(
                        "Retaining ${keeper.name}. Kept photos and duplicate-group representatives are protected.",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    TextButton(onClick = { open(keeper) }) { Text("Review retained photo") }
                }
            }
            items(group.candidates, key = { "$index-${it.id}" }) { photo ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(
                        checked = photo.id in selected,
                        onCheckedChange = { checked ->
                            selected = if (checked) selected + photo.id else selected - photo.id
                        },
                    )
                    Column(Modifier.weight(1f)) {
                        Text(photo.name, style = MaterialTheme.typography.titleSmall)
                        Text(
                            "${photo.album} · ${formatBytes(photo.bytes)}",
                            style = MaterialTheme.typography.bodySmall,
                        )
                        Row {
                            TextButton(onClick = { open(photo) }) { Text("Review") }
                            TextButton(
                                onClick = {
                                    model.decide(photo, "keep")
                                    selected = selected - photo.id
                                }
                            ) {
                                Text("Keep")
                            }
                        }
                    }
                }
                HorizontalDivider()
            }
        }
    }
}

private fun formatBytes(bytes: Long): String =
    when {
        bytes >= 1_073_741_824 -> "%.1f GB".format(bytes / 1_073_741_824.0)
        bytes >= 1_048_576 -> "%.1f MB".format(bytes / 1_048_576.0)
        else -> "${bytes/1024} KB"
    }
