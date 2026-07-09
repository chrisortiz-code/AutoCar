package com.autocar.app.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.unit.IntSize

private val GoldOverlay = Color(0xFFFFD700)

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

    Box(
        modifier = modifier
            .onGloballyPositioned { layoutSize = it.size }
            .then(
                if (draggable) {
                    Modifier.pointerInput(Unit) {
                        detectDragGestures(
                            onDragStart = { offset ->
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
                            },
                            onDragCancel = {
                                dragStart = null
                                dragCurrent = null
                            },
                        )
                    }
                } else {
                    Modifier
                }
            ),
    ) {
        MjpegView(url = url, modifier = Modifier.fillMaxWidth())

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
