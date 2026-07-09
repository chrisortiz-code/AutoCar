package com.autocar.app.ui.components

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
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
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit

private val sharedClient = OkHttpClient.Builder()
    .connectTimeout(5, TimeUnit.SECONDS)
    .readTimeout(10, TimeUnit.SECONDS)
    .build()

@Composable
fun MjpegView(url: String, modifier: Modifier = Modifier) {
    var bitmap by remember { mutableStateOf<android.graphics.Bitmap?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var retryCount by remember { mutableIntStateOf(0) }

    DisposableEffect(url, retryCount) {
        if (url.isBlank()) {
            error = "No URL"
            return@DisposableEffect onDispose {}
        }

        error = null
        val scope = CoroutineScope(Dispatchers.IO)
        val job: Job = scope.launch {
            // Retry loop — reconnects on timeout/disconnect
            while (isActive) {
                try {
                    val request = Request.Builder().url(url).build()
                    val response = sharedClient.newCall(request).execute()
                    if (!response.isSuccessful) {
                        error = "HTTP ${response.code}"
                        response.close()
                        delay(2000)
                        continue
                    }
                    val stream = BufferedInputStream(
                        response.body?.byteStream() ?: run {
                            error = "Empty response"
                            delay(2000)
                            continue
                        }
                    )
                    error = null
                    val buffer = ByteArrayOutputStream()
                    var inFrame = false

                    while (isActive) {
                        val b = stream.read()
                        if (b == -1) break

                        if (!inFrame) {
                            if (b == 0xFF) {
                                val next = stream.read()
                                if (next == -1) break
                                if (next == 0xD8) {
                                    buffer.reset()
                                    buffer.write(0xFF)
                                    buffer.write(0xD8)
                                    inFrame = true
                                }
                            }
                        } else {
                            buffer.write(b)
                            if (b == 0xD9) {
                                val bytes = buffer.toByteArray()
                                val len = bytes.size
                                if (len >= 2 && bytes[len - 2] == 0xFF.toByte()) {
                                    val decoded = BitmapFactory.decodeByteArray(bytes, 0, len)
                                    if (decoded != null) {
                                        bitmap = decoded
                                        error = null
                                    }
                                    inFrame = false
                                }
                            }
                        }
                    }
                    // Stream ended — retry after delay
                    stream.close()
                    response.close()
                } catch (_: kotlinx.coroutines.CancellationException) {
                    break
                } catch (e: Exception) {
                    error = e.message ?: "Connection failed"
                }
                if (isActive) delay(2000)
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
            error != null && currentBitmap == null -> Text(
                "Camera: $error",
                color = MaterialTheme.colorScheme.error,
            )
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
