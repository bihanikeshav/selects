# Selects for Android

Standalone, on-device photo organization and cleanup. No desktop server, account, or upload. Android 9 or later; recoverable gallery trash requires Android 11 or later.

## Gallery import and grouping

- Scan the accessible on-device gallery, choose complete albums, or bulk-select in a native gallery app. Samsung Gallery is preferred when installed; other devices use their gallery chooser.
- Camera and personal folders are eligible for automatic albums. Screenshots, messaging folders such as WhatsApp, social apps, and downloads/app-generated media are excluded by default. Persistent source switches let users include them. Classification uses folder names and MediaStore relative paths, never image contents. An explicit native-gallery or whole-folder selection is honored independently of automatic source filters.
- Existing named personal folders stay together. Camera and Expert RAW share a timeline. Broad date/location periods are combined using neighboring date gaps and available GPS to target at most 50 overview suggestions, rather than splitting every night. Unknown capture dates stay separate. If the user already has more than 50 named folders, their existing folder boundaries are preserved. No location names are invented. Dates fall back to file dates and use the phone's timezone.
- Confirm groups before adding references to the library. Select all groups, choose specific folders, merge groups, or rename them. Choosing a folder scopes the photos before grouping so a camera group cannot pull in unselected folders.
- Originals stay in place. The database holds content URIs, metadata, fingerprints, and reviews. New scans do not generate copies or JPEG previews. The grid uses Android thumbnails, with a bounded decode fallback. Old development-build previews can still be displayed.
- Keep/reject decisions survive rescans, source changes, and database upgrades. Size/modified stamps invalidate stale analysis; unchanged metadata and analysis skip image reads.

Android controls the accessible scope. Grant all-photo access for whole-phone scanning; selected-photo access remains limited and is labelled. Albums come from public MediaStore buckets. Secure folders, other profiles, and cloud-only images are outside that scope. The external gallery controls its own selection UI; the in-app browser supports complete-album selection regardless of the external UI.

Embedded photo-location access is optional and does not request live location. It enables GPS grouping and unredacted original-file hashing on Android 10+. Without it, date/album grouping, similarity, and low-detail checks still work. Failed metadata reads are retried on the next scan.

## Cleanup and progress

Gallery scans cache the previous catalog once, read metadata with at most four workers, and save checkpoints in transactions of 128 photos. Unchanged files reuse their metadata without opening originals. Progress updates do not reload the full catalog each time; album/group snapshots refresh at most every 10 seconds during work and immediately on completion. Pause/resume retains committed checkpoints.

Normal scans read original metadata only for eligible album sources. Other sources retain lightweight MediaStore inventory rows for cleanup without opening their originals. Progress counts eligible photos, not excluded app media. Whole-gallery cleanup deliberately reads and analyzes every accessible source. Database version 3 backfills relative paths without invalidating healthy analysis or reviews.

- Whole-gallery cleanup first refreshes the catalog, then analyzes orientation-correct previews bounded to 640 pixels in memory.
- Exact duplicates require SHA-256 of unredacted originals. Only same-size candidates need file hashing. Hashes are cached until source stamps change.
- Similarity uses a Hamming-distance index plus aspect, color, and detail checks. Every extra must match its retained representative, rather than chaining matches across a cluster. These are suggestions, not semantic judgments.
- Very low detail and explicitly rejected photos are separate categories. Nothing is preselected. Kept photos and a representative of every duplicate group are protected, including conservative matching of kept legacy picker references.
- The user reviews candidates and submits batches of up to 200 to Android's trash confirmation. There is no permanent-delete fallback. Canceled requests do not change the catalog. After approval, only an explicit `IS_TRASHED` flag counts as verified; unavailable/unverified items remain listed. Restore trashed photos using the native gallery.
- WorkManager persists background jobs. Scan, analysis, and identical-file checks show counts and progress. Pause/resume retains completed work. Long tasks retry in bounded time slices. Source access failures can be retried with Reprocess.

The app requests no internet access. Technical rankings are not the desktop aesthetic, face, or scene models. RAW decoding depends on the device. Video, semantic ranking, zoom, and export remain future work.

## Build

Open this directory in Android Studio. Install Android SDK 36, use JDK 17 or 21, and let Gradle sync. For a command-line build, set `ANDROID_HOME` or add your SDK path to untracked `local.properties`.

```sh
./gradlew assembleDebug testDebugUnitTest lintDebug
```

The APK is written to `app/build/outputs/apk/debug/app-debug.apk`. This is a development build, separate from the desktop 0.1.21 release.

## Next device checks

Test full/partial/denied photo access, Samsung Gallery multi-select, complete albums, GPS/no-GPS groups, confirmation and group edits, pause/restart/resume, and unchanged rescans. Review exact/similar/low-detail candidates and keep protection. Test trash cancellation and restoration using disposable test photos before distribution. Automated tests cover grouping, fingerprint indexing, keeper protection, staging, migration, source invalidation, original preservation, and explicit trash verification.

Reference: [MediaStore access](https://developer.android.com/training/data-storage/shared/media), [partial photo access](https://developer.android.com/about/versions/14/changes/partial-photo-video-access), [AGP compatibility](https://developer.android.com/build/releases/agp-8-13-0-release-notes).
