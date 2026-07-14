package com.autocar.app.ui.components

import android.graphics.BitmapFactory
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.unit.IntSize
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

private val GoldOverlay = Color(0xFFFFD700)

private val mjpegClient = OkHttpClient.Builder()
    .connectTimeout(5, TimeUnit.SECONDS)
    .readTimeout(10, TimeUnit.SECONDS)
    .build()

@Composable
fun DraggableMjpegView(
    url: String,
    draggable: Boolean,
    onBoxDrawn: (x1: Float, y1: Float, x2: Float, y2: Float) -> Unit,
    modifier: Modifier = Modifier,
) {
    var layoutSize by remember { mutableStateOf(IntSize.Zero) }
    var dragStart by remember { mutableStateOf<Offset?>(null) }
    var dragCurrent by remember { mutableStateOf<Offset?>(null) }
    var isDragging by remember { mutableStateOf(false) }

    // Inline MJPEG decoder so we can freeze the bitmap during drag
    var bitmap by remember { mutableStateOf<android.graphics.Bitmap?>(null) }
    var frozenBitmap by remember { mutableStateOf<android.graphics.Bitmap?>(null) }
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
            while (isActive) {
                try {
                    val request = Request.Builder().url(url).build()
                    val response = mjpegClient.newCall(request).execute()
                    val body = response.body
                    if (!response.isSuccessful || body == null) {
                        error = if (!response.isSuccessful) "HTTP ${response.code}" else "Empty response"
                        response.close()
                    } else {
                        val stream = BufferedInputStream(body.byteStream())
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
                        stream.close()
                        response.close()
                    }
                } catch (_: kotlinx.coroutines.CancellationException) {
                    break
                } catch (e: Exception) {
                    error = e.message ?: "Connection failed"
                }
                if (isActive) delay(2000)
            }
        }
        onDispose { job.cancel() }
    }

    // Show frozen bitmap while dragging, live bitmap otherwise
    val displayBitmap = if (isDragging) frozenBitmap ?: bitmap else bitmap

    Box(
        modifier = modifier
            .onGloballyPositioned { layoutSize = it.size }
            .then(
                if (draggable) {
                    Modifier.pointerInput(Unit) {
                        detectDragGestures(
                            onDragStart = { offset ->
                                isDragging = true
                                frozenBitmap = bitmap  // freeze current frame
                                dragStart = offset
                                dragCurrent = offset
                            },
                            onDrag = { change, _ ->
                                change.consume()
                                dragCurrent = change.position
                            },
                            onDragEnd = {
                                val start = dragStart
                                val end = dragCurrent
                                if (start != null && end != null &&
                                    layoutSize.width > 0 && layoutSize.height > 0
                                ) {
                                    val x1 = start.x / layoutSize.width
                                    val y1 = start.y / layoutSize.height
                                    val x2 = end.x / layoutSize.width
                                    val y2 = end.y / layoutSize.height
                                    onBoxDrawn(x1, y1, x2, y2)
                                }
                                dragStart = null
                                dragCurrent = null
                                isDragging = false
                                frozenBitmap = null
                            },
                            onDragCancel = {
                                dragStart = null
                                dragCurrent = null
                                isDragging = false
                                frozenBitmap = null
                            },
                        )
                    }
                } else {
                    Modifier
                }
            ),
    ) {
        // Inline image display (frozen or live)
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 200.dp),
            contentAlignment = androidx.compose.ui.Alignment.Center,
        ) {
            when {
                error != null && displayBitmap == null -> androidx.compose.material3.Text(
                    "Camera: $error",
                    color = androidx.compose.material3.MaterialTheme.colorScheme.error,
                )
                displayBitmap != null -> Image(
                    bitmap = displayBitmap.asImageBitmap(),
                    contentDescription = "Camera feed",
                    modifier = Modifier.fillMaxWidth(),
                    contentScale = ContentScale.FillWidth,
                )
                else -> androidx.compose.material3.CircularProgressIndicator()
            }
        }

        // Draw drag rectangle overlay
        val start = dragStart
        val current = dragCurrent
        if (start != null && current != null) {
            Canvas(modifier = Modifier.fillMaxSize()) {
                val topLeft = Offset(
                    x = minOf(start.x, current.x),
                    y = minOf(start.y, current.y),
                )
                val rectSize = Size(
                    width = kotlin.math.abs(current.x - start.x),
                    height = kotlin.math.abs(current.y - start.y),
                )
                // Semi-transparent fill
                drawRect(
                    color = GoldOverlay.copy(alpha = 0.2f),
                    topLeft = topLeft,
                    size = rectSize,
                )
                // Border
                drawRect(
                    color = GoldOverlay,
                    topLeft = topLeft,
                    size = rectSize,
                    style = Stroke(width = 3f),
                )
            }
        }
    }
}
