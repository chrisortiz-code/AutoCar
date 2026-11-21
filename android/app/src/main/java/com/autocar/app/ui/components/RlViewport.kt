package com.autocar.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.autocar.app.ui.theme.Gold
import com.autocar.app.ui.theme.StatusGreen
import com.autocar.app.ui.theme.StatusRed
import com.autocar.app.ui.theme.StatusYellow
import com.autocar.app.viewmodel.RlViewModel

@Composable
fun RlViewport(
    rlVm: RlViewModel = viewModel(),
    modifier: Modifier = Modifier,
) {
    val rlState by rlVm.rlState.collectAsState()
    val streamUrl by rlVm.streamUrl.collectAsState()
    val error by rlVm.error.collectAsState()
    val checkpoints by rlVm.checkpoints.collectAsState()
    val safetyStatus by rlVm.safetyStatus.collectAsState()
    var selectedCheckpoint by remember { mutableStateOf("") }
    var selectedBackend by remember { mutableStateOf("csrt") }

    val backends = listOf("csrt", "orb", "akaze", "sift", "lightglue")

    DisposableEffect(Unit) {
        onDispose { rlVm.onTabHidden() }
    }

    val status = rlState.status
    val isIdle = status == "idle"
    val isLoading = status == "loading"
    val isStreaming = status == "streaming"

    if (!isIdle && !isLoading && status != "") {
        // ── Active: stream + telemetry overlay ──
        Box(modifier = modifier.fillMaxSize()) {
            DraggableMjpegView(
                url = streamUrl,
                draggable = isStreaming,
                onBoxDrawn = { x1, y1, x2, y2 ->
                    rlVm.setReference(x1, y1, x2, y2)
                },
                modifier = Modifier.fillMaxSize(),
            )

            Box(modifier = Modifier.align(Alignment.TopEnd).padding(8.dp)) {
                RlStatusChip(status)
            }

            if (error != null) {
                Text(
                    text = "Error: $error",
                    color = StatusRed,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.align(Alignment.TopStart).padding(8.dp),
                )
            }

            // ── Telemetry footer ──
            Column(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .fillMaxWidth()
                    .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.9f))
                    .padding(horizontal = 8.dp, vertical = 6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                if (isStreaming) {
                    Text(
                        text = "Draw a box around the target to track",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Gold,
                    )
                }

                // Model info
                if (rlState.modelVariant.isNotEmpty() || rlState.modelName.isNotEmpty()) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceEvenly,
                    ) {
                        StatCell("Model", rlState.modelName.ifEmpty { rlState.modelVariant })
                        StatCell("Actions", "${rlState.actionDim}D")
                        StatCell("LSTM", "#${rlState.lstmStep}")
                        StatCell("FPS", "%.1f".format(rlState.fps))
                    }
                }

                // Latency breakdown bar
                if (rlState.totalMs > 0) {
                    Text("Pipeline", style = MaterialTheme.typography.labelSmall, color = Gold)
                    LatencyBar(
                        csrtMs = rlState.csrtMs,
                        policyMs = rlState.policyMs,
                        totalMs = rlState.totalMs,
                    )
                }

                // Action bars
                if (rlState.actions.isNotEmpty()) {
                    Text("Actions", style = MaterialTheme.typography.labelSmall, color = Gold)
                    val names = when (rlState.actionDim) {
                        2 -> listOf("Fwd", "Yaw")
                        else -> listOf("Fwd", "Strafe", "Yaw")
                    }
                    rlState.actions.forEachIndexed { i, v ->
                        ActionBar(label = names.getOrElse(i) { "A$i" }, value = v)
                    }
                }

                // Motor + depth row
                if (rlState.motorCmd.size == 4) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceEvenly,
                    ) {
                        StatCell("Vx", "%.2f".format(rlState.motorCmd[0]))
                        StatCell("Vy", "%.2f".format(rlState.motorCmd[1]))
                        StatCell("Spd", "%.2f".format(rlState.motorCmd[2]))
                        StatCell("Rot", "%.2f".format(rlState.motorCmd[3]))
                    }
                }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceEvenly,
                ) {
                    StatCell("Conf", "%.0f%%".format(rlState.confidence * 100))
                    StatCell(
                        "Target",
                        if (rlState.targetDepth > 0) "${rlState.targetDepth}mm" else "--",
                    )
                    StatCell(
                        "Depth",
                        if (rlState.curDepth > 0) "${rlState.curDepth}mm" else "--",
                    )
                    StatCell("Dist", "%.2fm".format(rlState.desiredDist))
                }

                if (rlState.safetyActive) {
                    Text(
                        "SAFETY ACTIVE",
                        color = StatusRed,
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.align(Alignment.CenterHorizontally),
                    )
                }

                // Buttons
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    OutlinedButton(
                        onClick = { rlVm.reset() },
                        modifier = Modifier.weight(1f),
                    ) { Text("Reset") }
                    Button(
                        onClick = { rlVm.stop() },
                        modifier = Modifier.weight(1f),
                        colors = ButtonDefaults.buttonColors(containerColor = StatusRed),
                    ) { Text("Stop", fontWeight = FontWeight.Bold, color = Color.White) }
                }
            }
        }
    } else {
        // ── Idle / Loading ──
        Column(
            modifier = modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 8.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("RL Policy", style = MaterialTheme.typography.titleLarge, color = Gold)
                RlStatusChip(status)
            }

            if (error != null) {
                Text("Error: $error", color = StatusRed, style = MaterialTheme.typography.bodySmall)
            }

            if (isIdle) {
                Text(
                    "CNN+LSTM policy with CSRT tracking at ~4 Hz.\n" +
                        "Draw a bounding box on the stream to set the target.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                if (checkpoints.isNotEmpty()) {
                    ModelSelector(
                        checkpoints = checkpoints,
                        selected = selectedCheckpoint,
                        onSelect = { selectedCheckpoint = it },
                    )
                }

                BackendSelector(
                    backends = backends,
                    selected = selectedBackend,
                    onSelect = { selectedBackend = it },
                )

                // Safety toggle
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column {
                        Text("Safety Filter", style = MaterialTheme.typography.bodyMedium)
                        Text(
                            if (safetyStatus.enabled) "Enabled — nearest ${safetyStatus.nearestMm}mm"
                            else "Disabled",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Switch(
                        checked = safetyStatus.enabled,
                        onCheckedChange = { rlVm.toggleSafety() },
                        colors = SwitchDefaults.colors(checkedTrackColor = Gold),
                    )
                }

                Button(
                    onClick = { rlVm.start(checkpoint = selectedCheckpoint, backend = selectedBackend) },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Gold),
                ) { Text("Start RL", fontWeight = FontWeight.Bold, color = Color.Black) }
            } else if (isLoading) {
                Column(
                    modifier = Modifier.fillMaxWidth().padding(vertical = 32.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(16.dp),
                ) {
                    CircularProgressIndicator(color = Gold)
                    Text(
                        text = rlState.statusMsg.ifEmpty { "Loading model..." },
                        style = MaterialTheme.typography.bodyMedium,
                        color = Gold,
                    )
                }
                Button(
                    onClick = { rlVm.stop() },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = StatusRed),
                ) { Text("Cancel", fontWeight = FontWeight.Bold, color = Color.White) }
            }
        }
    }
}

// ── Private composables ──

@Composable
private fun RlStatusChip(status: String) {
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
private fun StatCell(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            value,
            style = MaterialTheme.typography.titleSmall,
            fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.onSurface,
        )
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun LatencyBar(csrtMs: Float, policyMs: Float, totalMs: Float) {
    val max = maxOf(totalMs, 1f)
    val csrtFrac = (csrtMs / max).coerceIn(0f, 1f)
    val policyFrac = (policyMs / max).coerceIn(0f, 1f)
    val otherFrac = (1f - csrtFrac - policyFrac).coerceAtLeast(0f)

    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .weight(1f)
                .height(16.dp)
                .clip(RoundedCornerShape(4.dp))
                .background(MaterialTheme.colorScheme.surfaceVariant),
        ) {
            Row(modifier = Modifier.fillMaxSize()) {
                if (csrtFrac > 0f) {
                    Box(
                        modifier = Modifier
                            .fillMaxHeight()
                            .weight(csrtFrac.coerceAtLeast(0.01f))
                            .background(Color(0xFF42A5F5)),
                        contentAlignment = Alignment.Center,
                    ) {
                        if (csrtFrac > 0.15f) {
                            Text("CSRT", fontSize = 7.sp, color = Color.White, maxLines = 1)
                        }
                    }
                }
                if (policyFrac > 0f) {
                    Box(
                        modifier = Modifier
                            .fillMaxHeight()
                            .weight(policyFrac.coerceAtLeast(0.01f))
                            .background(Color(0xFFAB47BC)),
                        contentAlignment = Alignment.Center,
                    ) {
                        if (policyFrac > 0.15f) {
                            Text("Policy", fontSize = 7.sp, color = Color.White, maxLines = 1)
                        }
                    }
                }
                if (otherFrac > 0.01f) {
                    Spacer(Modifier.weight(otherFrac).fillMaxHeight())
                }
            }
        }
        Spacer(Modifier.width(6.dp))
        Text(
            "%.0fms".format(totalMs),
            style = MaterialTheme.typography.labelSmall,
            fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun ActionBar(label: String, value: Float) {
    val clamped = value.coerceIn(-1f, 1f)
    val barColor = if (clamped >= 0) StatusGreen else StatusRed

    Row(
        modifier = Modifier.fillMaxWidth().height(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            modifier = Modifier.width(40.dp),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Box(
            modifier = Modifier
                .weight(1f)
                .fillMaxHeight()
                .clip(RoundedCornerShape(3.dp))
                .background(MaterialTheme.colorScheme.surfaceVariant),
        ) {
            // Two halves: left half for negative, right half for positive
            Row(modifier = Modifier.fillMaxSize()) {
                // Left half (negative)
                Box(modifier = Modifier.weight(1f).fillMaxHeight()) {
                    if (clamped < 0) {
                        Box(
                            modifier = Modifier
                                .fillMaxHeight()
                                .fillMaxWidth(kotlin.math.abs(clamped))
                                .align(Alignment.CenterEnd)
                                .background(barColor),
                        )
                    }
                }
                // Right half (positive)
                Box(modifier = Modifier.weight(1f).fillMaxHeight()) {
                    if (clamped > 0) {
                        Box(
                            modifier = Modifier
                                .fillMaxHeight()
                                .fillMaxWidth(clamped)
                                .align(Alignment.CenterStart)
                                .background(barColor),
                        )
                    }
                }
            }
            // Center tick
            Box(
                modifier = Modifier
                    .width(1.dp)
                    .fillMaxHeight()
                    .align(Alignment.Center)
                    .background(Gold.copy(alpha = 0.5f)),
            )
        }
        Text(
            "%+.2f".format(value),
            modifier = Modifier.width(48.dp),
            style = MaterialTheme.typography.labelSmall,
            fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.onSurface,
            textAlign = TextAlign.End,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ModelSelector(
    checkpoints: List<String>,
    selected: String,
    onSelect: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val display = if (selected.isEmpty()) "Default (${checkpoints.firstOrNull() ?: "none"})"
        else selected.removeSuffix(".pt")

    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = it },
    ) {
        OutlinedTextField(
            value = display,
            onValueChange = {},
            readOnly = true,
            label = { Text("Model") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = Gold,
                focusedLabelColor = Gold,
            ),
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        ExposedDropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            DropdownMenuItem(
                text = { Text("Default") },
                onClick = { onSelect(""); expanded = false },
            )
            checkpoints.forEach { ckpt ->
                DropdownMenuItem(
                    text = { Text(ckpt.removeSuffix(".pt")) },
                    onClick = { onSelect(ckpt); expanded = false },
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BackendSelector(
    backends: List<String>,
    selected: String,
    onSelect: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }

    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = it },
    ) {
        OutlinedTextField(
            value = selected.uppercase(),
            onValueChange = {},
            readOnly = true,
            label = { Text("Tracker") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = Gold,
                focusedLabelColor = Gold,
            ),
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        ExposedDropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            backends.forEach { backend ->
                DropdownMenuItem(
                    text = { Text(backend.uppercase()) },
                    onClick = { onSelect(backend); expanded = false },
                )
            }
        }
    }
}
