package com.autocar.app.ui.components

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream

@Composable
fun MjpegView(url: String, modifier: Modifier = Modifier) {
    var bitmap by remember { mutableStateOf<android.graphics.Bitmap?>(null) }
    var error by remember { mutableStateOf<String?>(null) }

    DisposableEffect(url) {
        if (url.isBlank()) {
            error = "No URL"
            return@DisposableEffect onDispose {}
        }

        val client = OkHttpClient.Builder().build()
        val scope = CoroutineScope(Dispatchers.IO)
        val job: Job = scope.launch {
            try {
                val request = Request.Builder().url(url).build()
                val response = client.newCall(request).execute()
                val stream = BufferedInputStream(response.body?.byteStream() ?: return@launch)
                val buffer = ByteArrayOutputStream()
                var inFrame = false

                while (true) {
                    val b = stream.read()
                    if (b == -1) break

                    if (!inFrame) {
                        // Look for JPEG SOI marker: 0xFF 0xD8
                        if (b == 0xFF) {
                            val next = stream.read()
                            if (next == 0xD8) {
                                buffer.reset()
                                buffer.write(0xFF)
                                buffer.write(0xD8)
                                inFrame = true
                            }
                        }
                    } else {
                        buffer.write(b)
                        // Look for JPEG EOI marker: 0xFF 0xD9
                        if (b == 0xD9) {
                            val bytes = buffer.toByteArray()
                            val len = bytes.size
                            if (len >= 2 && bytes[len - 2] == 0xFF.toByte()) {
                                val decoded = BitmapFactory.decodeByteArray(bytes, 0, len)
                                if (decoded != null) {
                                    bitmap = decoded
                                }
                                inFrame = false
                            }
                        }
                    }
                }
            } catch (e: Exception) {
                error = e.message
            }
        }

        onDispose {
            job.cancel()
        }
    }

    Box(
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = 200.dp),
        contentAlignment = Alignment.Center,
    ) {
        val currentBitmap = bitmap
        when {
            error != null -> Text("Camera error: $error")
            currentBitmap != null -> Image(
                bitmap = currentBitmap.asImageBitmap(),
                contentDescription = "Camera feed",
                modifier = Modifier.fillMaxWidth(),
                contentScale = ContentScale.FillWidth,
            )
            else -> CircularProgressIndicator()
        }
    }
}
