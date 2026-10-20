package app.selects.mobile

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.io.File

data class Photo(
    val id: Long, val uri: String, val name: String, val status: String,
    val preview: String?, val score: Int?, val reason: String?,
    val decision: String, val error: String?,
)

class PhotoStore(context: Context) : SQLiteOpenHelper(context, "selects.db", null, 1) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("""CREATE TABLE photos (
            id INTEGER PRIMARY KEY, uri TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued', preview TEXT, score INTEGER,
            reason TEXT, decision TEXT NOT NULL DEFAULT 'unreviewed', error TEXT,
            analysis_version INTEGER NOT NULL DEFAULT 0
        )""")
        db.execSQL("CREATE INDEX photos_status ON photos(status,id)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    fun add(uri: String, name: String): Boolean = writableDatabase.insertWithOnConflict(
        "photos", null, ContentValues().apply { put("uri", uri); put("name", name) },
        SQLiteDatabase.CONFLICT_IGNORE,
    ) != -1L

    private fun query(where: String = ""): List<Photo> = readableDatabase.rawQuery(
        "SELECT id,uri,name,status,preview,score,reason,decision,error FROM photos $where", null,
    ).use { cursor ->
        buildList {
            while (cursor.moveToNext()) add(Photo(
                cursor.getLong(0), cursor.getString(1), cursor.getString(2), cursor.getString(3),
                cursor.getString(4), if (cursor.isNull(5)) null else cursor.getInt(5),
                cursor.getString(6), cursor.getString(7), cursor.getString(8),
            ))
        }
    }

    fun photos(): List<Photo> = query("ORDER BY score DESC,id")

    fun nextQueued(): Photo? = query("WHERE status='queued' ORDER BY id LIMIT 1").firstOrNull()

    fun complete(id: Long, preview: String, quality: Quality) {
        writableDatabase.update("photos", ContentValues().apply {
            put("status", "ready"); put("preview", preview); put("score", quality.score)
            put("reason", quality.reason)
            put("analysis_version", QualityAnalyzer.VERSION); putNull("error")
        }, "id=?", arrayOf(id.toString()))
    }

    fun fail(id: Long, message: String) {
        writableDatabase.update("photos", ContentValues().apply {
            put("status", "error"); put("error", message)
        }, "id=?", arrayOf(id.toString()))
    }

    fun decide(id: Long, decision: String) {
        require(decision in setOf("keep", "reject", "unreviewed"))
        writableDatabase.update("photos", ContentValues().apply { put("decision", decision) },
            "id=?", arrayOf(id.toString()))
    }

    /** Healthy rows are left alone. Review decisions survive repair and upgrades. */
    fun queueRepairs(): Int {
        val db = writableDatabase
        db.beginTransaction()
        return try {
            var count = db.update("photos", ContentValues().apply {
                put("status", "queued"); putNull("error")
            }, "status!='queued' AND (status='error' OR analysis_version<? OR preview IS NULL)",
                arrayOf(QualityAnalyzer.VERSION.toString()))
            for (photo in query("WHERE status='ready'")) {
                if (photo.preview != null && !File(photo.preview).isFile) {
                    count += db.update("photos", ContentValues().apply { put("status", "queued") },
                        "id=?", arrayOf(photo.id.toString()))
                }
            }
            db.setTransactionSuccessful()
            count
        } finally { db.endTransaction() }
    }
}
