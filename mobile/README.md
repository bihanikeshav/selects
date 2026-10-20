# Selects for Android

Standalone Android app, starting with a local photo review workflow. No desktop server, account, or upload is required. Android 9 or later.

## Current slice

- Import selected images through the system photo picker, with persistent read access.
- Process the queue on the phone with WorkManager, including after the app restarts.
- Decode orientation-correct previews at a maximum of 640 pixels on the long edge.
- Rank by image detail and clipped exposure, with a plain-language explanation.
- Keep or reject photos, with decisions stored in SQLite.
- Open a photo for a larger, orientation-correct review.
- Reprocess failed or outdated analysis and missing previews. Healthy photos skip decoding and hashing.

Originals are read only. The app stores previews and review data in private storage and requests no network or whole-library permissions. Source photos must remain available. Re-select a photo if its provider revokes access.

These are technical suggestions, not the desktop aesthetic, face, or scene models. RAW support depends on the device decoder. Video, semantic ranking, burst grouping, zoom, and export are future work.

## Build

Open this directory in Android Studio. Install Android SDK 36, use JDK 17 or 21, and let Gradle sync. For a command-line build, set `ANDROID_HOME` or add your SDK path to untracked `local.properties`.

```sh
./gradlew assembleDebug testDebugUnitTest lintDebug
```

The APK is written to `app/build/outputs/apk/debug/app-debug.apk`. This is a development build, separate from the desktop 0.1.21 release.

## Next device checks

Import JPEG/HEIC photos, rotate the phone during processing, close and reopen the app, keep and reject photos, and re-import the same selection. Confirm that completed previews and decisions survive, errors can be repaired, and a healthy reprocess does no image work. Check picker behavior and background processing on a physical Android device before distributing builds.

Reference: [Android photo picker](https://developer.android.com/training/data-storage/shared/photo-picker), [AGP compatibility](https://developer.android.com/build/releases/agp-8-13-0-release-notes).
