package com.autocar.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
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
import com.autocar.app.viewmodel.TrackViewModel

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ObjectTrackViewport(
    trackVm: TrackViewModel = viewModel(),
    modifier: Modifier = Modifier,
    selectedBackend: String = "orb",
    onBackendChange: ((String) -> Unit)? = null,
    followMode: Boolean = true,
    onFollowModeChange: ((Boolean) -> Unit)? = null,
) {
    val trackState by trackVm.trackState.collectAsState()
    val streamUrl by trackVm.streamUrl.collectAsState()
    val error by trackVm.error.collectAsState()

    DisposableEffect(Unit) {
        onDispose { trackVm.onTabHidden() }
    }

    // Use hoisted state if callbacks provided, otherwise fall back to local state
    var localBackend by remember { mutableStateOf(selectedBackend) }
    var localFollow by remember { mutableStateOf(followMode) }
    val activeBackend = if (onBackendChange != null) selectedBackend else localBackend
    val activeFollow = if (onFollowModeChange != null) followMode else localFollow
    val setBackend: (String) -> Unit = onBackendChange ?: { localBackend = it }
    val setFollow: (Boolean) -> Unit = onFollowModeChange ?: { localFollow = it }

    val status = trackState.status
    val isIdle = status == "idle"
    val isLoading = status == "loading"
    val isStreaming = status == "streaming"

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
                text = "Object Tracking",
                style = MaterialTheme.typography.titleLarge,
                color = Gold,
            )
            TrackStatusChip(status)
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
            // ── Backend selector ──
            Text("Backend", style = MaterialTheme.typography.labelLarge, color = Gold)
            val backends = listOf("orb", "akaze", "sift", "template")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                backends.forEach { backend ->
                    FilterChip(
                        selected = activeBackend == backend,
                        onClick = { setBackend(backend) },
                        label = { Text(backend) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = GoldDark.copy(alpha = 0.3f),
                            selectedLabelColor = Gold,
                        ),
                    )
                }
            }

            // ── Follow toggle ──
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Follow mode", style = MaterialTheme.typography.labelLarge, color = Gold)
                Switch(
                    checked = activeFollow,
                    onCheckedChange = { setFollow(it) },
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = Gold,
                        checkedTrackColor = GoldDark.copy(alpha = 0.4f),
                    ),
                )
            }

            // ── Start button ──
            Button(
                onClick = {
                    trackVm.start(backend = activeBackend, follow = activeFollow)
                },
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(containerColor = Gold),
            ) {
                Text("Start", fontWeight = FontWeight.Bold, color = Color.Black)
            }
        } else if (isLoading) {
            // ── Loading state ──
            Column(
                modifier = Modifier.fillMaxWidth().padding(vertical = 32.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                CircularProgressIndicator(color = Gold)
                Text(
                    text = trackState.status_msg.ifEmpty { "Loading..." },
                    style = MaterialTheme.typography.bodyMedium,
                    color = Gold,
                )
            }

            // Stop button during loading
            Button(
                onClick = { trackVm.stop() },
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(containerColor = StatusRed),
            ) {
                Text("Cancel", fontWeight = FontWeight.Bold, color = Color.White)
            }
        } else {
            // ── Active states: stream + controls ──

            // Stream with drag-to-select
            DraggableMjpegView(
                url = streamUrl,
                draggable = isStreaming,
                onBoxDrawn = { x1, y1, x2, y2 ->
                    trackVm.setReference(x1, y1, x2, y2)
                },
                modifier = Modifier.fillMaxWidth(),
            )

            // Prompt when no reference set
            if (isStreaming) {
                Text(
                    text = "Draw a box around the target to track",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Gold,
                    modifier = Modifier.padding(vertical = 4.dp),
                )
            }

            // Stats row
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly,
            ) {
                TrackStatLabel("Conf", "%.0f%%".format(trackState.confidence * 100))
                TrackStatLabel("FPS", "%.1f".format(trackState.fps))
                TrackStatLabel("Rot", "%.2f".format(trackState.rot_speed))
                TrackStatLabel("Vx", "%.2f".format(trackState.vx))
            }

            // Action buttons
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedButton(
                    onClick = { trackVm.reset() },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Reset")
                }

                Button(
                    onClick = { trackVm.stop() },
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.buttonColors(containerColor = StatusRed),
                ) {
                    Text("Stop", fontWeight = FontWeight.Bold, color = Color.White)
                }
            }
        }
    }
}

@Composable
private fun TrackStatusChip(status: String) {
    val (color, label) = when (status) {
        "idle" -> Pair(MaterialTheme.colorScheme.onSurfaceVariant, "Idle")
        "loading" -> Pair(StatusYellow, "Loading")
        "streaming" -> Pair(StatusYellow, "Streaming")
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
private fun TrackStatLabel(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(value, style = MaterialTheme.typography.titleMedium)
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
