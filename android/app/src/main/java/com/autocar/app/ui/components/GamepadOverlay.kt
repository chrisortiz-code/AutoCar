package com.autocar.app.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import com.autocar.app.data.gamepad.GamepadState
import com.autocar.app.ui.theme.StatusGreen

@Composable
fun GamepadOverlay(state: GamepadState, modifier: Modifier = Modifier) {
    Card(
        modifier = modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = if (state.connected) "Gamepad Connected" else "No Gamepad",
                    style = MaterialTheme.typography.labelMedium,
                    color = if (state.connected) StatusGreen else Color.Gray,
                )
                Text(
                    text = "R2: ${(state.r2 * 100).toInt()}%",
                    style = MaterialTheme.typography.labelMedium,
                )
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp),
                horizontalArrangement = Arrangement.SpaceEvenly,
            ) {
                StickIndicator(label = "L", x = state.leftX, y = state.leftY)
                StickIndicator(label = "R", x = state.rightX, y = state.rightY)
            }
        }
    }
}

@Composable
private fun StickIndicator(label: String, x: Float, y: Float) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(text = label, style = MaterialTheme.typography.labelMedium)
        Canvas(modifier = Modifier.size(60.dp)) {
            val center = Offset(size.width / 2, size.height / 2)
            val radius = size.width / 2

            // Outer circle
            drawCircle(
                color = Color.Gray,
                radius = radius,
                center = center,
                style = Stroke(width = 2f),
            )

            // Stick position dot
            val dotX = center.x + x * radius * 0.8f
            val dotY = center.y + y * radius * 0.8f
            drawCircle(
                color = StatusGreen,
                radius = 6f,
                center = Offset(dotX, dotY),
            )
        }
    }
}
