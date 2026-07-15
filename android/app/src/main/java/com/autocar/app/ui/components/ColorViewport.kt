package com.autocar.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.autocar.app.ui.theme.Gold
import com.autocar.app.ui.theme.GoldDark
import com.autocar.app.ui.theme.StatusGreen
import com.autocar.app.ui.theme.StatusRed
import com.autocar.app.ui.theme.StatusYellow
import com.autocar.app.viewmodel.ColorViewModel

@Composable
fun ColorViewport(
    colorVm: ColorViewModel = viewModel(),
    modifier: Modifier = Modifier,
    selectedMode: String = "trace",
    onModeChange: ((String) -> Unit)? = null,
) {
    val colorState by colorVm.colorState.collectAsState()
    val streamUrl by colorVm.streamUrl.collectAsState()
    val error by colorVm.error.collectAsState()

    DisposableEffect(Unit) {
        onDispose { colorVm.onTabHidden() }
    }

    var localMode by remember { mutableStateOf(selectedMode) }
    val activeMode = if (onModeChange != null) selectedMode else localMode
    val setMode: (String) -> Unit = onModeChange ?: { localMode = it }

    val status = colorState.status
    val isIdle = status == "idle"
    val isLoading = status == "loading"
    val isStreaming = status == "streaming"
    val isPicking = status == "picking"

    if (!isIdle && !isLoading && status != "") {
        // ── Active states: stream fills space, controls pinned at bottom ──
        Box(modifier = modifier.fillMaxSize()) {
            // Stream fills entire viewport
            TappableMjpegView(
                url = streamUrl,
                tappable = isStreaming || isPicking,
                onTap = { x, y -> colorVm.clickColor(x, y) },
                modifier = Modifier.fillMaxSize(),
            )

            // Status chip — top right
            Box(modifier = Modifier.align(Alignment.TopEnd).padding(8.dp)) {
                ColorStatusChip(status)
            }

            // Error — top left
            if (error != null) {
                Text(
                    text = "Error: $error",
                    color = StatusRed,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.align(Alignment.TopStart).padding(8.dp),
                )
            }

            // ── Footer: pinned controls ──
            Column(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .fillMaxWidth()
                    .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.85f))
                    .padding(horizontal = 8.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                // Streaming prompt
                if (isStreaming) {
                    Text(
                        text = "Tap a color to pick",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Gold,
                    )
                }

                // Picking: swatch + depth + confirm/re-pick
                if (isPicking) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        val rgb = colorState.picked_color
                        val swatchColor = if (rgb != null && rgb.size == 3)
                            Color(rgb[0], rgb[1], rgb[2])
                        else Color.Gray
                        Box(
                            modifier = Modifier
                                .size(36.dp)
                                .background(swatchColor, RoundedCornerShape(6.dp))
                                .border(1.dp, Color.White, RoundedCornerShape(6.dp)),
                        )

                        val depthText = if (colorState.target_depth != null)
                            "%.2f m".format(colorState.target_depth!! / 1000f)
                        else "no depth"
                        Text(
                            text = "Depth: $depthText",
                            style = MaterialTheme.typography.bodyMedium,
                            color = StatusGreen,
                        )
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        OutlinedButton(
                            onClick = { colorVm.reset() },
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("Re-pick")
                        }
                        Button(
                            onClick = { colorVm.confirm() },
                            modifier = Modifier.weight(1f),
                            colors = ButtonDefaults.buttonColors(containerColor = StatusGreen),
                        ) {
                            Text("Confirm", fontWeight = FontWeight.Bold, color = Color.Black)
                        }
                    }
                }

                // Stats row
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceEvenly,
                ) {
                    ColorStatLabel("Conf", "%.0f%%".format(colorState.confidence * 100))
                    ColorStatLabel("FPS", "%.1f".format(colorState.fps))
                    ColorStatLabel("Rot", "%.2f".format(colorState.rot_speed))
                    ColorStatLabel("Vx", "%.2f".format(colorState.vx))
                }

                // Action buttons
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    OutlinedButton(
                        onClick = { colorVm.reset() },
                        modifier = Modifier.weight(1f),
                    ) {
                        Text("Reset")
                    }
                    Button(
                        onClick = { colorVm.stop() },
                        modifier = Modifier.weight(1f),
                        colors = ButtonDefaults.buttonColors(containerColor = StatusRed),
                    ) {
                        Text("Stop", fontWeight = FontWeight.Bold, color = Color.White)
                    }
                }
            }
        }
    } else {
        // ── Idle / Loading states: scrollable column ──
        Column(
            modifier = modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 8.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // ── Header ──
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Color Tracking",
                    style = MaterialTheme.typography.titleLarge,
                    color = Gold,
                )
                ColorStatusChip(status)
            }

            // ── Error ──
            if (error != null) {
                Text(
                    text = "Error: $error",
                    color = StatusRed,
                    style = MaterialTheme.typography.bodySmall,
                )
            }

            if (isIdle) {
                // ── Mode selector ──
                Text("Mode", style = MaterialTheme.typography.labelLarge, color = Gold)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ColorModeButton("Trace", activeMode == "trace") { setMode("trace") }
                    ColorModeButton("Follow", activeMode == "follow") { setMode("follow") }
                }

                // ── Start button ──
                Button(
                    onClick = { colorVm.start(follow = activeMode == "follow") },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Gold),
                ) {
                    Text("Start", fontWeight = FontWeight.Bold, color = Color.Black)
                }
            } else if (isLoading) {
                Column(
                    modifier = Modifier.fillMaxWidth().padding(vertical = 32.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(16.dp),
                ) {
                    CircularProgressIndicator(color = Gold)
                    Text(
                        text = colorState.status_msg.ifEmpty { "Loading..." },
                        style = MaterialTheme.typography.bodyMedium,
                        color = Gold,
                    )
                }
                Button(
                    onClick = { colorVm.stop() },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = StatusRed),
                ) {
                    Text("Cancel", fontWeight = FontWeight.Bold, color = Color.White)
                }
            }
        }
    }
}

@Composable
private fun ColorStatusChip(status: String) {
    val (color, label) = when (status) {
        "idle" -> Pair(MaterialTheme.colorScheme.onSurfaceVariant, "Idle")
        "loading" -> Pair(StatusYellow, "Loading")
        "streaming" -> Pair(StatusGreen, "Streaming")
        "picking" -> Pair(Gold, "Picked")
        "tracking" -> Pair(StatusGreen, "Tracking")
        "lost" -> Pair(StatusRed, "Lost")
        else -> Pair(MaterialTheme.colorScheme.onSurfaceVariant, status)
    }
    Box(
        modifier = Modifier
            .border(1.dp, color, RoundedCornerShape(12.dp))
            .padding(horizontal = 10.dp, vertical = 4.dp),
    ) {
        Text(label, color = color, style = MaterialTheme.typography.labelMedium)
    }
}

@Composable
private fun ColorModeButton(label: String, selected: Boolean, onClick: () -> Unit) {
    OutlinedButton(
        onClick = onClick,
        colors = ButtonDefaults.outlinedButtonColors(
            containerColor = if (selected) GoldDark.copy(alpha = 0.3f) else Color.Transparent,
            contentColor = if (selected) Gold else MaterialTheme.colorScheme.onSurfaceVariant,
        ),
        border = ButtonDefaults.outlinedButtonBorder(selected),
    ) {
        Text(label)
    }
}

@Composable
private fun ColorStatLabel(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(value, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onSurface)
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
