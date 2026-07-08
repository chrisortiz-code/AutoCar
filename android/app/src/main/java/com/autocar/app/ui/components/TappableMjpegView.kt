package com.autocar.app.ui.components

import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.unit.IntSize
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue

@Composable
fun TappableMjpegView(
    url: String,
    tappable: Boolean,
    onTap: (x: Float, y: Float) -> Unit,
    modifier: Modifier = Modifier,
) {
    var layoutSize by remember { mutableStateOf(IntSize.Zero) }

    Box(
        modifier = modifier
            .onGloballyPositioned { layoutSize = it.size }
            .then(
                if (tappable) {
                    Modifier.pointerInput(Unit) {
                        detectTapGestures { offset ->
                            if (layoutSize.width > 0 && layoutSize.height > 0) {
                                val normX = offset.x / layoutSize.width
                                val normY = offset.y / layoutSize.height
                                onTap(normX, normY)
                            }
                        }
                    }
                } else {
                    Modifier
                }
            ),
    ) {
        MjpegView(url = url)
    }
}
