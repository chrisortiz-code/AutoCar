package com.autocar.app.ui.screens

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.SportsEsports
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.autocar.app.data.gamepad.GamepadManager
import com.autocar.app.ui.components.GamepadOverlay
import com.autocar.app.ui.theme.Gold
import com.autocar.app.ui.theme.GoldDark
import com.autocar.app.ui.theme.GoldLight
import com.autocar.app.ui.theme.StatusGreen
import com.autocar.app.ui.theme.StatusRed
import com.autocar.app.ui.theme.StatusYellow
import com.autocar.app.viewmodel.DriveViewModel
import com.autocar.app.viewmodel.SensorsViewModel

@Composable
fun DriveScreen(
    gamepadManager: GamepadManager,
    driveVm: DriveViewModel = viewModel(),
    sensorsVm: SensorsViewModel = viewModel(),
) {
    val gamepadState by gamepadManager.state.collectAsState()
    val driveStatus by driveVm.driveStatus.collectAsState()
    val sensorUpdate by sensorsVm.sensorUpdate.collectAsState()
    val gamepadDriving by driveVm.gamepadDriving.collectAsState()
    val r3HoldProgress by driveVm.r3HoldProgress.collectAsState()

    var touchVx by remember { mutableFloatStateOf(0f) }
    var touchVy by remember { mutableFloatStateOf(0f) }
    var trail by remember { mutableStateOf(listOf<Offset>()) }
    var dragging by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        driveVm.connectWebSocket()
        driveVm.startGamepadDriving(gamepadManager)
    }

    DisposableEffect(Unit) {
        onDispose {
            driveVm.deactivateGamepadDriving()
        }
    }

    Box(modifier = Modifier.fillMaxSize()) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Drive Control", style = MaterialTheme.typography.headlineLarge, color = Gold)

        // Controller drive status banner
        AnimatedVisibility(
            visible = gamepadDriving,
            enter = fadeIn(),
            exit = fadeOut(),
        ) {
            Surface(
                color = StatusGreen.copy(alpha = 0.15f),
                shape = RoundedCornerShape(8.dp),
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.Center,
                ) {
                    Icon(
                        Icons.Default.SportsEsports,
                        contentDescription = null,
                        tint = StatusGreen,
                        modifier = Modifier.size(18.dp),
                    )
                    Text(
                        "  Controller Drive Active",
                        color = StatusGreen,
                        fontWeight = FontWeight.Bold,
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Text(
                        "  —  tap R3 to stop",
                        color = StatusGreen.copy(alpha = 0.7f),
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        }

        // Compact motor status bar
        sensorUpdate.motors?.let { motorsData ->
            Surface(
                color = MaterialTheme.colorScheme.surfaceVariant,
                shape = MaterialTheme.shapes.small,
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.SpaceEvenly,
                ) {
                    for (nid in 0..3) {
                        val motor = motorsData.motors[nid.toString()]
                        val isConn = nid in motorsData.connected
                        val hasError = (motor?.error ?: 0) != 0
                        val isArmed = motor?.armed ?: false
                        val color = when {
                            hasError -> StatusRed
                            !isConn -> StatusRed
                            isArmed -> StatusGreen
                            else -> StatusYellow
                        }
                        val role = motor?.role ?: listOf("BL", "FL", "BR", "FR").getOrElse(nid) { "?" }
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(8.dp)
                                    .clip(CircleShape)
                                    .background(color)
                            )
                            Text(
                                text = if (isConn && motor != null) "$role %.1fA".format(motor.current) else "$role --",
                                style = MaterialTheme.typography.labelSmall,
                            )
                        }
                    }
                }
            }
        }

        if (gamepadDriving) {
            GamepadOverlay(state = gamepadState)
        }

        Text(
            text = if (driveStatus.moving) "Status: Moving" else "Status: Idle",
            style = MaterialTheme.typography.bodyLarge,
        )

        Spacer(Modifier.height(8.dp))

        Text("Touch to drive:", style = MaterialTheme.typography.titleLarge, color = Gold)
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(200.dp)
                .clip(MaterialTheme.shapes.medium)
                .pointerInput(Unit) {
                    detectDragGestures(
                        onDragStart = { offset ->
                            dragging = true
                            trail = listOf(offset)
                        },
                        onDragEnd = {
                            touchVx = 0f
                            touchVy = 0f
                            dragging = false
                            trail = emptyList()
                            driveVm.sendStop()
                        },
                        onDragCancel = {
                            touchVx = 0f
                            touchVy = 0f
                            dragging = false
                            trail = emptyList()
                            driveVm.sendStop()
                        },
                    ) { change, dragAmount ->
                        touchVx = (-dragAmount.y / 200f).coerceIn(-1f, 1f)
                        touchVy = (dragAmount.x / 200f).coerceIn(-1f, 1f)
                        driveVm.sendDrive(touchVx, touchVy, 3f, 0f)
                        // Append position, keep last 20 points for the trail
                        trail = (trail + change.position).takeLast(20)
                    }
                },
            contentAlignment = Alignment.Center,
        ) {
            Surface(
                modifier = Modifier.fillMaxSize(),
                color = MaterialTheme.colorScheme.surfaceVariant,
                shape = MaterialTheme.shapes.medium,
            ) {
                Box(contentAlignment = Alignment.Center) {
                    if (!dragging) {
                        Text("Drag to drive")
                    }
                }
            }
            // Gold meteor trail overlay
            if (trail.size >= 2) {
                Canvas(modifier = Modifier.fillMaxSize()) {
                    val points = trail
                    for (i in points.indices) {
                        val t = (i + 1).toFloat() / points.size  // 0→1 (tail→head)
                        val radius = 3.dp.toPx() + t * 9.dp.toPx()
                        val alpha = t * t  // quadratic fade — head is bright
                        // Glow halo
                        drawCircle(
                            brush = Brush.radialGradient(
                                colors = listOf(
                                    Gold.copy(alpha = alpha * 0.5f),
                                    Color.Transparent,
                                ),
                                center = points[i],
                                radius = radius * 2.5f,
                            ),
                            radius = radius * 2.5f,
                            center = points[i],
                        )
                        // Core
                        drawCircle(
                            color = GoldLight.copy(alpha = alpha),
                            radius = radius * 0.5f,
                            center = points[i],
                        )
                        drawCircle(
                            color = Gold.copy(alpha = alpha),
                            radius = radius,
                            center = points[i],
                        )
                    }
                }
            }
        }

        Spacer(Modifier.height(8.dp))

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Button(
                onClick = { driveVm.sendStop() },
                modifier = Modifier.weight(1f),
            ) {
                Icon(Icons.Default.Stop, contentDescription = null, modifier = Modifier.size(20.dp))
                Text(" Stop", modifier = Modifier.padding(start = 4.dp))
            }

            Button(
                onClick = { driveVm.sendEstop() },
                modifier = Modifier.weight(1f),
                colors = ButtonDefaults.buttonColors(containerColor = StatusRed),
            ) {
                Icon(Icons.Default.Warning, contentDescription = null, modifier = Modifier.size(20.dp), tint = Color.White)
                Text(" E-STOP", modifier = Modifier.padding(start = 4.dp), color = Color.White)
            }
        }
    } // end Column

    // R3 hold countdown overlay
    AnimatedVisibility(
        visible = r3HoldProgress > 0f && !gamepadDriving,
        enter = fadeIn(),
        exit = fadeOut(),
        modifier = Modifier.fillMaxSize(),
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black.copy(alpha = 0.6f)),
            contentAlignment = Alignment.Center,
        ) {
            // Countdown ring
            Canvas(modifier = Modifier.size(120.dp)) {
                // Background ring
                drawArc(
                    color = GoldDark.copy(alpha = 0.4f),
                    startAngle = -90f,
                    sweepAngle = 360f,
                    useCenter = false,
                    style = Stroke(width = 8.dp.toPx(), cap = StrokeCap.Round),
                )
                // Progress ring
                drawArc(
                    color = Gold,
                    startAngle = -90f,
                    sweepAngle = r3HoldProgress * 360f,
                    useCenter = false,
                    style = Stroke(width = 8.dp.toPx(), cap = StrokeCap.Round),
                )
            }
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Icon(
                    Icons.Default.SportsEsports,
                    contentDescription = null,
                    tint = Gold,
                    modifier = Modifier.size(32.dp),
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    "Hold R3",
                    color = Gold,
                    fontWeight = FontWeight.Bold,
                    fontSize = 16.sp,
                )
                Text(
                    "${((1f - r3HoldProgress) * 5).toInt() + 1}s",
                    color = Gold.copy(alpha = 0.7f),
                    fontSize = 14.sp,
                )
            }
        }
    }
    } // end Box
}
