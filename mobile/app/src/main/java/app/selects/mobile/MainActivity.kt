package app.selects.mobile

import android.graphics.BitmapFactory
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts.PickMultipleVisualMedia
import androidx.activity.result.contract.ActivityResultContracts.PickVisualMedia
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.core.net.toUri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            // A photographer reviewing outdoors and later at home needs the device's chosen theme.
            val scheme = if (isSystemInDarkTheme()) darkColorScheme(
                primary = Color(0xFFD1C6AA), background = Color(0xFF181815), surface = Color(0xFF181815),
            ) else lightColorScheme(
                primary = Color(0xFF655B47), background = Color(0xFFFAF9F4), surface = Color(0xFFFAF9F4),
            )
            MaterialTheme(colorScheme = scheme) { LibraryScreen() }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LibraryScreen(model: LibraryViewModel = viewModel()) {
    val state by model.state.collectAsStateWithLifecycle()
    var filter by remember { mutableStateOf("All") }
    var reviewingId by remember { mutableStateOf<Long?>(null) }
    val reviewing = state.photos.firstOrNull { it.id == reviewingId }
    androidx.activity.compose.BackHandler(enabled = reviewing != null) { reviewingId = null }
    val picker = rememberLauncherForActivityResult(PickMultipleVisualMedia()) { model.import(it) }
    val import = { picker.launch(PickVisualMediaRequest(PickVisualMedia.ImageOnly)) }
    Scaffold(topBar = {
        TopAppBar(title = { Text(if (reviewing == null) "Selects" else "Review photo") },
            navigationIcon = {
                if (reviewing != null) TextButton(onClick = { reviewingId = null }) { Text("Back") }
            }, actions = {
            TextButton(onClick = { model.repair() }, enabled = !state.processing && !state.importing) {
                Text("Reprocess")
            }
        })
    }, floatingActionButton = {
        if (state.photos.isNotEmpty() && !state.importing && reviewing == null) ExtendedFloatingActionButton(
            onClick = import, text = { Text("Import photos") }, icon = { Text("+") },
        )
    }) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(horizontal = 16.dp)) {
            state.message?.let { Text(it, modifier = Modifier.padding(vertical = 8.dp), color = MaterialTheme.colorScheme.primary) }
            if (reviewing != null) {
                ReviewPhoto(reviewing, model::decide)
            } else if (state.photos.isEmpty() && !state.importing) {
                Column(Modifier.weight(1f).fillMaxWidth(), verticalArrangement = Arrangement.Center) {
                    Text("Find your keepers", style = MaterialTheme.typography.headlineMedium)
                    Spacer(Modifier.height(12.dp))
                    Text("Choose photos for a first pass on your phone. Review the suggestions and keep the final say.")
                    Spacer(Modifier.height(24.dp))
                    Button(onClick = import) { Text("Import photos") }
                    Spacer(Modifier.height(12.dp))
                    Text("On-device processing. Originals stay untouched.", style = MaterialTheme.typography.bodySmall)
                }
            } else {
                val remaining = state.photos.count { it.status == "queued" }
                Text("Your photos", style = MaterialTheme.typography.headlineMedium)
                Text("${state.photos.size} imported · ${state.photos.count { it.decision == "keep" }} kept",
                    style = MaterialTheme.typography.bodyMedium)
                if (state.processing || state.importing) {
                    Spacer(Modifier.height(12.dp))
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                    Text(if (state.importing) "Adding photos…" else "Processing on this phone · $remaining remaining",
                        style = MaterialTheme.typography.bodySmall)
                }
                Row(Modifier.padding(vertical = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("All", "Picks", "Kept").forEach { label ->
                        FilterChip(selected = filter == label, onClick = { filter = label }, label = { Text(label) })
                    }
                }
                if (filter == "Picks") Text("Technical ranking by detail and exposure. Review every suggestion.",
                    style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(bottom = 12.dp))
                val visible = state.photos.filter {
                    when (filter) {
                        "Kept" -> it.decision == "keep"
                        "Picks" -> it.status == "ready" && it.decision != "reject"
                        else -> true
                    }
                }
                if (visible.isEmpty()) Text(if (filter == "Kept") "Photos you keep will appear here." else "No photos to show yet.")
                LazyVerticalGrid(GridCells.Adaptive(160.dp), verticalArrangement = Arrangement.spacedBy(20.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(bottom = 100.dp)) {
                    items(visible, key = { it.id }) { photo ->
                        PhotoTile(photo, model::decide) { reviewingId = photo.id }
                    }
                }
            }
        }
    }
}

@Composable
private fun PhotoTile(photo: Photo, decide: (Photo, String) -> Unit, open: () -> Unit) {
    val bitmap by produceState<ImageBitmap?>(null, photo.preview, photo.status) {
        value = withContext(Dispatchers.IO) {
            photo.preview?.let { BitmapFactory.decodeFile(it)?.asImageBitmap() }
        }
    }
    Column {
        Surface(Modifier.fillMaxWidth().aspectRatio(1f).clickable(onClickLabel = "Review ${photo.name}", onClick = open),
            color = MaterialTheme.colorScheme.surfaceVariant,
            shape = MaterialTheme.shapes.small) {
            val image = bitmap
            if (image != null) Image(image, contentDescription = photo.name, contentScale = ContentScale.Crop)
            else Box(contentAlignment = Alignment.Center) { Text(if (photo.status == "error") "Unavailable" else "Queued") }
        }
        Text(photo.name, maxLines = 1, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp))
        Text(photo.error ?: photo.reason ?: "Waiting for analysis", style = MaterialTheme.typography.bodySmall)
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            FilterChip(selected = photo.decision == "keep", onClick = {
                decide(photo, if (photo.decision == "keep") "unreviewed" else "keep")
            }, label = { Text("Keep") })
            FilterChip(selected = photo.decision == "reject", onClick = {
                decide(photo, if (photo.decision == "reject") "unreviewed" else "reject")
            }, label = { Text("Reject") })
        }
    }
}

@Composable
private fun ReviewPhoto(photo: Photo, decide: (Photo, String) -> Unit) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val source by produceState<Pair<Boolean, ImageBitmap?>>(true to null, photo.uri) {
        val bitmap = withContext(Dispatchers.IO) {
            try {
                android.graphics.ImageDecoder.decodeBitmap(android.graphics.ImageDecoder.createSource(
                    context.contentResolver, photo.uri.toUri(),
                )) { decoder, info, _ ->
                    decoder.allocator = android.graphics.ImageDecoder.ALLOCATOR_SOFTWARE
                    val scale = (1600.0 / maxOf(info.size.width, info.size.height)).coerceAtMost(1.0)
                    decoder.setTargetSize(maxOf(1, (info.size.width * scale).toInt()),
                        maxOf(1, (info.size.height * scale).toInt()))
                }.asImageBitmap()
            } catch (_: Exception) { null }
        }
        value = false to bitmap
    }
    // The source can be revoked independently of the saved review and preview.
    Column(Modifier.fillMaxSize()) {
        Text(photo.name, style = MaterialTheme.typography.titleMedium)
        Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
            source.second?.let { Image(it, contentDescription = photo.name, modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Fit) } ?: if (source.first) {
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                } else Text("Photo unavailable. Re-select it to restore access.")
        }
        Text(photo.error ?: photo.reason ?: "Waiting for analysis", modifier = Modifier.padding(vertical = 12.dp))
        Row(Modifier.fillMaxWidth().padding(bottom = 16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            FilterChip(selected = photo.decision == "keep", onClick = {
                decide(photo, if (photo.decision == "keep") "unreviewed" else "keep")
            }, label = { Text("Keep") })
            FilterChip(selected = photo.decision == "reject", onClick = {
                decide(photo, if (photo.decision == "reject") "unreviewed" else "reject")
            }, label = { Text("Reject") })
        }
    }
}
