# Recover missing photo metadata

After updating Selects, open **Libraries** and click **Re-index** for the affected
library. The indexer retries metadata from older readers even when the file has not
changed. It fills missing photo dates, GPS coordinates, camera details and video dates without
overwriting existing values or recreating previews and cached photo analysis.
The full pipeline then rebuilds groups that depend on the recovered metadata.
Successful reads are versioned, including files with no embedded tags. Subsequent
reindexes skip their metadata reads entirely until the file or reader changes.
Failed reads remain eligible for retry.

For metadata recovery without running the later pipeline stages:

```console
selects index "C:/path/to/library" --pass index
```

Run the normal full index afterward to update date and location groupings:

```console
selects index "C:/path/to/library"
```

Photos without those embedded tags remain unchanged. Unreadable metadata produces
a warning and can be retried on the next index. Video dates come from container
creation tags, with a direct MP4/MOV movie-header fallback when ffprobe is unavailable.
Timezone-aware video timestamps are stored as UTC. Files without embedded dates
remain undated; filesystem modification times are not used as capture dates.
