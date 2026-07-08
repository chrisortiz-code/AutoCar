package com.autocar.app.ui.screens

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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.autocar.app.data.gamepad.GamepadManager
import com.autocar.app.ui.components.GamepadOverlay
import com.autocar.app.ui.theme.StatusRed
import com.autocar.app.viewmodel.DriveViewModel

@Composable
fun DriveScreen(
    gamepadManager: GamepadManager,
    driveVm: DriveViewModel = viewModel(),
) {
    val gamepadState by gamepadManager.state.collectAsState()
    val driveStatus by driveVm.driveStatus.collectAsState()

    // Touch control state
    var touchVx by remember { mutableFloatStateOf(0f) }
    var touchVy by remember { mutableFloatStateOf(0f) }

    LaunchedEffect(Unit) {
        driveVm.connectWebSocket()
        driveVm.startGamepadDriving(gamepadManager)
    }

    DisposableEffect(Unit) {
        onDispose {
            driveVm.sendStop()
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Drive Control", style = MaterialTheme.typography.headlineLarge)

        // Gamepad overlay
        GamepadOverlay(state = gamepadState)

        // Status
        Text(
            text = if (driveStatus.moving) "Status: Moving" else "Status: Idle",
            style = MaterialTheme.typography.bodyLarge,
        )

        Spacer(Modifier.height(8.dp))

        // Touch drive pad
        Text("Touch to drive:", style = MaterialTheme.typography.titleLarge)
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(200.dp)
                .pointerInput(Unit) {
                    detectDragGestures(
                        onDragEnd = {
                            touchVx = 0f
                            touchVy = 0f
                            driveVm.sendStop()
                        },
                        onDragCancel = {
                            touchVx = 0f
                            touchVy = 0f
                            driveVm.sendStop()
                        },
                    ) { _, dragAmount ->
                        touchVx = (-dragAmount.y / 200f).coerceIn(-1f, 1f)
                        touchVy = (dragAmount.x / 200f).coerceIn(-1f, 1f)
                        driveVm.sendDrive(touchVx, touchVy, 3f, 0f)
                    }
                },
            contentAlignment = Alignment.Center,
        ) {
            androidx.compose.material3.Surface(
                modifier = Modifier.fillMaxSize(),
                color = MaterialTheme.colorScheme.surfaceVariant,
                shape = MaterialTheme.shapes.medium,
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Text("Drag to drive")
                }
            }
        }

        Spacer(Modifier.height(8.dp))

        // Stop / E-stop buttons
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
    }
}
