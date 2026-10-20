package app.selects.mobile

import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.content.pm.ProviderInfo
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.Bundle
import android.os.CancellationSignal
import android.provider.MediaStore
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowContentResolver

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class TrashVerificationTest {
    private class Provider : ContentProvider() {
        var flag: Int? = null
        var includeTrash = false

        override fun onCreate() = true

        override fun getType(uri: Uri): String = "image/jpeg"

        override fun query(
            uri: Uri,
            projection: Array<out String>?,
            selection: String?,
            selectionArgs: Array<out String>?,
            sortOrder: String?,
        ): Cursor =
            MatrixCursor(arrayOf(MediaStore.Images.Media.IS_TRASHED)).apply {
                flag?.let { addRow(arrayOf(it)) }
            }

        override fun query(
            uri: Uri,
            projection: Array<out String>?,
            queryArgs: Bundle?,
            cancellationSignal: CancellationSignal?,
        ): Cursor {
            includeTrash =
                queryArgs?.getInt(MediaStore.QUERY_ARG_MATCH_TRASHED) == MediaStore.MATCH_INCLUDE
            return query(uri, projection, null, null, null)
        }

        override fun insert(uri: Uri, values: ContentValues?): Uri? =
            throw AssertionError("Verification must never write originals")

        override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int =
            throw AssertionError("Verification must never delete originals")

        override fun update(
            uri: Uri,
            values: ContentValues?,
            selection: String?,
            selectionArgs: Array<out String>?,
        ): Int = throw AssertionError("Verification must never modify originals")
    }

    @Test
    fun onlyAnExplicitTrashFlagCountsAsVerified() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val provider = Provider()
        provider.attachInfo(context, ProviderInfo().apply { authority = "media" })
        ShadowContentResolver.registerProviderInternal("media", provider)
        val uri = Uri.parse("content://media/external/images/media/1")
        assertFalse(MediaAccess.isTrashed(context, uri))
        provider.flag = 0
        assertFalse(MediaAccess.isTrashed(context, uri))
        provider.flag = 1
        assertTrue(MediaAccess.isTrashed(context, uri))
        assertTrue(provider.includeTrash)
    }
}
