package com.autocar.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.autocar.app.ui.components.MjpegView
import com.autocar.app.ui.theme.StatusGreen
import com.autocar.app.ui.theme.StatusRed
import com.autocar.app.viewmodel.SensorsViewModel

@Composable
fun SensorsScreen(sensorsVm: SensorsViewModel = viewModel()) {
    val sensorUpdate by sensorsVm.sensorUpdate.collectAsState()
    val cameraUrl by sensorsVm.cameraUrl.collectAsState()

    LaunchedEffect(Unit) {
        sensorsVm.connect()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Sensors", style = MaterialTheme.typography.headlineLarge)

        // Camera feed
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(12.dp)) {
                Text("Camera", style = MaterialTheme.typography.titleLarge)
                val cam = sensorUpdate.camera
                if (cam != null) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(
                            text = if (cam.connected) "Connected" else "Disconnected",
                            color = if (cam.connected) StatusGreen else StatusRed,
                        )
                        if (cam.connected) {
                            Text("%.0f FPS".format(cam.fps))
                        }
                    }
                }
                MjpegView(url = cameraUrl)
            }
        }

        // Lidar status
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(12.dp)) {
                Text("Lidar", style = MaterialTheme.typography.titleLarge)
                val lidar = sensorUpdate.lidar
                if (lidar != null) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(
                            text = if (lidar.connected) "Connected" else "Disconnected",
                            color = if (lidar.connected) StatusGreen else StatusRed,
                        )
                        if (lidar.connected) {
                            Text("%.1f Hz".format(lidar.scan_hz))
                        }
                    }
                    Text("Points: ${lidar.points.size}")
                } else {
                    Text("Waiting for data...")
                }
            }
        }

        // Drive mode
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(12.dp)) {
                Text("Drive", style = MaterialTheme.typography.titleLarge)
                val drive = sensorUpdate.drive
                if (drive != null) {
                    Text("Mode: ${drive.mode}")
                    Text(if (drive.moving) "Moving" else "Idle")
                } else {
                    Text("Waiting for data...")
                }
            }
        }
    }
}
