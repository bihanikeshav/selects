package app.selects.mobile

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.io.File

data class Photo(
    val id: Long,
    val uri: String,
    val name: String,
    val status: String,
    val preview: String?,
    val score: Int?,
    val reason: String?,
    val decision: String,
    val error: String?,
    val taken: Long = 0,
    val album: String = "",
    val albumId: String = "",
    val bytes: Long = 0,
    val modified: Long = 0,
    val latitude: Double? = null,
    val longitude: Double? = null,
    val imported: Boolean = true,
    val source: String = "picked",
    val collection: String = "",
    val sha: String? = null,
    val visualHash: String? = null,
    val visual: String? = null,
    val detail: Double? = null,
    val clipped: Double? = null,
    val aspect: Double = 0.0,
    val metadataVersion: Int = 0,
)

data class MediaEntry(
    val uri: String,
    val name: String,
    val taken: Long = 0,
    val album: String = "Gallery",
    val albumId: String = "",
    val bytes: Long = 0,
    val modified: Long = 0,
    val latitude: Double? = null,
    val longitude: Double? = null,
    val metadataRead: Boolean = false,
)

data class JobProgress(
    val key: String,
    val stage: String,
    val done: Int,
    val total: Int,
    val status: String,
) {
    val fraction: Float
        get() = if (total > 0) (done.toFloat() / total).coerceIn(0f, 1f) else 0f
}

class PhotoStore(context: Context) : SQLiteOpenHelper(context, "selects.db", null, 2) {
    init {
        setWriteAheadLoggingEnabled(true)
    }

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """CREATE TABLE photos (id INTEGER PRIMARY KEY, uri TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',preview TEXT,score INTEGER,
            reason TEXT,decision TEXT NOT NULL DEFAULT 'unreviewed',error TEXT,
            analysis_version INTEGER NOT NULL DEFAULT 0)"""
        )
        db.execSQL("CREATE INDEX photos_status ON photos(status,id)")
        gallerySchema(db)
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        if (oldVersion < 2) gallerySchema(db)
    }

    private fun gallerySchema(db: SQLiteDatabase) {
        listOf(
                "taken INTEGER NOT NULL DEFAULT 0",
                "album TEXT NOT NULL DEFAULT ''",
                "album_id TEXT NOT NULL DEFAULT ''",
                "bytes INTEGER NOT NULL DEFAULT 0",
                "modified INTEGER NOT NULL DEFAULT 0",
                "latitude REAL",
                "longitude REAL",
                "imported INTEGER NOT NULL DEFAULT 1",
                "source TEXT NOT NULL DEFAULT 'picked'",
                "collection TEXT NOT NULL DEFAULT ''",
                "sha TEXT",
                "visual_hash TEXT",
                "visual TEXT",
                "detail REAL",
                "clipped REAL",
                "aspect REAL NOT NULL DEFAULT 0",
                "metadata_version INTEGER NOT NULL DEFAULT 0",
                "present INTEGER NOT NULL DEFAULT 1",
                "seen TEXT NOT NULL DEFAULT ''",
            )
            .forEach { db.execSQL("ALTER TABLE photos ADD COLUMN $it") }
        db.execSQL("CREATE INDEX photos_gallery ON photos(source,present,album_id)")
        db.execSQL(
            "CREATE TABLE jobs (key TEXT PRIMARY KEY,stage TEXT NOT NULL,done INTEGER NOT NULL,total INTEGER NOT NULL,status TEXT NOT NULL)"
        )
    }

    fun add(uri: String, name: String): Boolean =
        writableDatabase.insertWithOnConflict(
            "photos",
            null,
            ContentValues().apply {
                put("uri", uri)
                put("name", name)
            },
            SQLiteDatabase.CONFLICT_IGNORE,
        ) != -1L

    private fun Cursor.photo(): Photo {
        fun s(k: String): String? = getString(getColumnIndexOrThrow(k))
        fun n(k: String): Long = getLong(getColumnIndexOrThrow(k))
        fun d(k: String): Double? =
            getColumnIndexOrThrow(k).let { if (isNull(it)) null else getDouble(it) }
        return Photo(
            n("id"),
            s("uri")!!,
            s("name")!!,
            s("status")!!,
            s("preview"),
            d("score")?.toInt(),
            s("reason"),
            s("decision")!!,
            s("error"),
            n("taken"),
            s("album")!!,
            s("album_id")!!,
            n("bytes"),
            n("modified"),
            d("latitude"),
            d("longitude"),
            n("imported") == 1L,
            s("source")!!,
            s("collection")!!,
            s("sha"),
            s("visual_hash"),
            s("visual"),
            d("detail"),
            d("clipped"),
            d("aspect") ?: 0.0,
            n("metadata_version").toInt(),
        )
    }

    private fun query(where: String = "", args: Array<String>? = null): List<Photo> =
        readableDatabase.rawQuery("SELECT * FROM photos $where", args).use { c ->
            buildList { while (c.moveToNext()) add(c.photo()) }
        }

    fun photos(): List<Photo> = query("WHERE present=1 AND imported=1 ORDER BY score DESC,id")

    fun catalog(): List<Photo> = query("WHERE present=1 ORDER BY taken,id")

    fun find(uri: String): Photo? = query("WHERE uri=?", arrayOf(uri)).firstOrNull()

    fun nextQueued(): Photo? =
        query("WHERE status='queued' AND present=1 ORDER BY id LIMIT 1").firstOrNull()

    fun queuedCount(): Int =
        readableDatabase
            .rawQuery("SELECT COUNT(*) FROM photos WHERE status='queued' AND present=1", null)
            .use {
                it.moveToFirst()
                it.getInt(0)
            }

    fun scanSnapshot(): Map<String, Photo> = query().associateBy { it.uri }

    fun upsertBatch(
        entries: List<MediaEntry>,
        seen: String,
        version: Int,
        previous: Map<String, Photo>,
    ) = transaction {
        entries.forEach { entry ->
            upsertKnown(entry, seen, if (entry.metadataRead) version else 0, previous[entry.uri])
        }
    }

    fun upsert(e: MediaEntry, seen: String, metadataVersion: Int = 1): Long =
        upsertKnown(e, seen, metadataVersion, find(e.uri))

    private fun upsertKnown(e: MediaEntry, seen: String, metadataVersion: Int, old: Photo?): Long {
        val changed = old != null && (old.bytes != e.bytes || old.modified != e.modified)
        val values =
            ContentValues().apply {
                put("uri", e.uri)
                put("name", e.name)
                put("taken", e.taken)
                put("album", e.album)
                put("album_id", e.albumId)
                put("bytes", e.bytes)
                put("modified", e.modified)
                put("latitude", e.latitude)
                put("longitude", e.longitude)
                put(
                    "source",
                    if (e.uri.startsWith("content://media/") && "/images/media/" in e.uri) "gallery"
                    else "picked",
                )
                put("present", 1)
                put("seen", seen)
                put("metadata_version", metadataVersion)
                if (old == null) {
                    put("imported", 0)
                    put("status", "catalog")
                }
                if (changed) {
                    put("status", if (old!!.status == "catalog") "catalog" else "queued")
                    put("analysis_version", 0)
                    listOf(
                            "sha",
                            "visual_hash",
                            "visual",
                            "score",
                            "detail",
                            "clipped",
                            "reason",
                            "error",
                        )
                        .forEach { putNull(it) }
                }
            }
        return if (old == null) writableDatabase.insertOrThrow("photos", null, values)
        else {
            writableDatabase.update("photos", values, "id=?", arrayOf(old.id.toString()))
            old.id
        }
    }

    fun finishScan(seen: String) {
        // Partial-access scans mark unavailable rows, never delete data or decisions.
        writableDatabase.update(
            "photos",
            ContentValues().apply { put("present", 0) },
            "source='gallery' AND seen!=?",
            arrayOf(seen),
        )
    }

    fun confirmGroups(groups: List<SmartGroup>) = transaction {
        groups.forEach { g ->
            g.photos.forEach { p ->
                writableDatabase.update(
                    "photos",
                    ContentValues().apply {
                        put("imported", 1)
                        put("collection", g.name)
                    },
                    "id=?",
                    arrayOf(p.id.toString()),
                )
                queue(p.id)
            }
        }
    }

    private fun queue(id: Long) {
        writableDatabase.execSQL(
            """UPDATE photos SET status='queued',error=NULL
        WHERE id=? AND (status IN ('catalog','error') OR analysis_version<?)""",
            arrayOf<Any>(id, QualityAnalyzer.VERSION),
        )
    }

    fun queueGallery() = transaction {
        catalog().filter { it.source == "gallery" }.forEach { queue(it.id) }
    }

    fun complete(id: Long, preview: String?, quality: Quality, signature: VisualSignature? = null) {
        writableDatabase.update(
            "photos",
            ContentValues().apply {
                put("status", "ready")
                put("preview", preview)
                put("score", quality.score)
                put("reason", quality.reason)
                put("detail", quality.detail)
                put("clipped", quality.clipped)
                if (signature != null) {
                    put("visual_hash", signature.hash)
                    put("visual", signature.colors)
                    put("aspect", signature.aspect)
                }
                put("analysis_version", QualityAnalyzer.VERSION)
                putNull("error")
            },
            "id=?",
            arrayOf(id.toString()),
        )
    }

    fun hashCandidates(): List<Photo> =
        query(
            """WHERE present=1 AND source='gallery' AND status='ready'
        AND sha IS NULL AND bytes>0 AND bytes IN
        (SELECT bytes FROM photos WHERE present=1 AND source='gallery' GROUP BY bytes HAVING COUNT(*)>1)"""
        )

    fun saveHash(id: Long, hash: String) {
        writableDatabase.update(
            "photos",
            ContentValues().apply { put("sha", hash) },
            "id=?",
            arrayOf(id.toString()),
        )
    }

    fun fail(id: Long, message: String) {
        writableDatabase.update(
            "photos",
            ContentValues().apply {
                put("status", "error")
                put("error", message)
            },
            "id=?",
            arrayOf(id.toString()),
        )
    }

    fun decide(id: Long, decision: String) {
        require(decision in setOf("keep", "reject", "unreviewed"))
        writableDatabase.update(
            "photos",
            ContentValues().apply { put("decision", decision) },
            "id=?",
            arrayOf(id.toString()),
        )
    }

    fun markUnavailable(ids: List<Long>) = transaction {
        ids.forEach { id ->
            writableDatabase.update(
                "photos",
                ContentValues().apply { put("present", 0) },
                "id=?",
                arrayOf(id.toString()),
            )
        }
    }

    fun queueRepairs(): Int = transaction {
        val count =
            writableDatabase.update(
                "photos",
                ContentValues().apply {
                    put("status", "queued")
                    putNull("error")
                },
                "present=1 AND status NOT IN ('queued','catalog') AND (status='error' OR analysis_version<?)",
                arrayOf(QualityAnalyzer.VERSION.toString()),
            )
        query("WHERE present=1 AND status='ready'").forEach { p ->
            if (p.preview != null && !File(p.preview).isFile)
                writableDatabase.update(
                    "photos",
                    ContentValues().apply { putNull("preview") },
                    "id=?",
                    arrayOf(p.id.toString()),
                )
        }
        count
    }

    fun progress(key: String, stage: String, done: Int, total: Int, status: String = "running") {
        writableDatabase.insertWithOnConflict(
            "jobs",
            null,
            ContentValues().apply {
                put("key", key)
                put("stage", stage)
                put("done", done)
                put("total", total)
                put("status", status)
            },
            SQLiteDatabase.CONFLICT_REPLACE,
        )
    }

    fun jobs(): List<JobProgress> =
        readableDatabase.rawQuery("SELECT * FROM jobs", null).use { c ->
            buildList {
                while (c.moveToNext()) add(
                    JobProgress(
                        c.getString(0),
                        c.getString(1),
                        c.getInt(2),
                        c.getInt(3),
                        c.getString(4),
                    )
                )
            }
        }

    private fun <T> transaction(action: () -> T): T {
        writableDatabase.beginTransaction()
        return try {
            action().also { writableDatabase.setTransactionSuccessful() }
        } finally {
            writableDatabase.endTransaction()
        }
    }
}
