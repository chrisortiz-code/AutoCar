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
import com.autocar.app.viewmodel.FaceViewModel

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun FaceViewport(
    faceVm: FaceViewModel = viewModel(),
    modifier: Modifier = Modifier,
    selectedBackend: String = "mediapipe",
    onBackendChange: ((String) -> Unit)? = null,
    selectedMode: String = "trace",
    onModeChange: ((String) -> Unit)? = null,
) {
    val faceState by faceVm.faceState.collectAsState()
    val streamUrl by faceVm.streamUrl.collectAsState()
    val error by faceVm.error.collectAsState()

    // Stop polling when leaving this tab
    DisposableEffect(Unit) {
        onDispose { faceVm.onTabHidden() }
    }

    // Use hoisted state if callbacks provided, otherwise fall back to local state
    var localMode by remember { mutableStateOf(selectedMode) }
    var localBackend by remember { mutableStateOf(selectedBackend) }
    val activeMode = if (onModeChange != null) selectedMode else localMode
    val activeBackend = if (onBackendChange != null) selectedBackend else localBackend
    val setMode: (String) -> Unit = onModeChange ?: { localMode = it }
    val setBackend: (String) -> Unit = onBackendChange ?: { localBackend = it }

    val status = faceState.status
    val isIdle = status == "idle"
    val isLoading = status == "loading"
    val isSelecting = status == "selecting"
    val isConfirming = status == "confirming"

    if (!isIdle && !isLoading && status != "") {
        // ── Active states: stream fills space, controls pinned at bottom ──
        Box(modifier = modifier.fillMaxSize()) {
            // Stream fills entire viewport
            TappableMjpegView(
                url = streamUrl,
                tappable = isSelecting || isConfirming,
                onTap = { x, y -> faceVm.clickFace(x, y) },
                modifier = Modifier.fillMaxSize(),
            )

            // Status chip — top right
            Box(modifier = Modifier.align(Alignment.TopEnd).padding(8.dp)) {
                StatusChip(status)
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
                // Selecting prompt
                if (isSelecting) {
                    Text(
                        text = "Tap a face to follow",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Gold,
                    )
                }

                // Confirming: show depth and confirm button
                if (isConfirming) {
                    val depthText = if (faceState.target_depth != null)
                        "%.2f m".format(faceState.target_depth!! / 1000f)
                    else "no depth"

                    Text(
                        text = "Locked depth: $depthText",
                        style = MaterialTheme.typography.bodyMedium,
                        color = StatusGreen,
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        OutlinedButton(
                            onClick = { faceVm.clickFace(0f, 0f) },
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("Re-select")
                        }

                        Button(
                            onClick = { faceVm.confirmFace() },
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
                    StatLabel("FPS", "%.1f".format(faceState.fps))
                    StatLabel("Faces", "${faceState.faces}")
                    StatLabel("Rot", "%.2f".format(faceState.rot_speed))
                    StatLabel("Vx", "%.2f".format(faceState.vx))
                }

                // Action buttons
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    OutlinedButton(
                        onClick = { faceVm.reset() },
                        modifier = Modifier.weight(1f),
                    ) {
                        Text("Reset")
                    }

                    Button(
                        onClick = { faceVm.stop() },
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
                    text = "Face Detection",
                    style = MaterialTheme.typography.titleLarge,
                    color = Gold,
                )
                StatusChip(status)
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
                    ModeButton("Trace", activeMode == "trace") { setMode("trace") }
                    ModeButton("Follow", activeMode == "follow") { setMode("follow") }
                }

                // ── Backend selector ──
                Text("Backend", style = MaterialTheme.typography.labelLarge, color = Gold)
                val backends = listOf("mediapipe", "yolo", "yunet", "scrfd")
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

                // ── Start button ──
                Button(
                    onClick = {
                        faceVm.start(
                            backend = activeBackend,
                            follow = activeMode == "follow",
                        )
                    },
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
                        text = faceState.status_msg.ifEmpty { "Loading..." },
                        style = MaterialTheme.typography.bodyMedium,
                        color = Gold,
                    )
                }

                Button(
                    onClick = { faceVm.stop() },
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
private fun StatusChip(status: String) {
    val (color, label) = when (status) {
        "idle" -> Pair(MaterialTheme.colorScheme.onSurfaceVariant, "Idle")
        "loading" -> Pair(StatusYellow, "Loading")
        "tracing" -> Pair(StatusGreen, "Tracing")
        "selecting" -> Pair(StatusYellow, "Selecting")
        "confirming" -> Pair(Gold, "Confirm?")
        "following" -> Pair(StatusGreen, "Following")
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
private fun ModeButton(label: String, selected: Boolean, onClick: () -> Unit) {
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
private fun StatLabel(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(value, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onSurface)
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
