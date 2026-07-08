package com.autocar.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.autocar.app.ui.components.ConnectionBar
import com.autocar.app.ui.theme.StatusGreen
import com.autocar.app.ui.theme.StatusRed
import com.autocar.app.viewmodel.SettingsViewModel

@Composable
fun SettingsScreen(settingsVm: SettingsViewModel = viewModel()) {
    val host by settingsVm.host.collectAsState()
    val port by settingsVm.port.collectAsState()
    val token by settingsVm.token.collectAsState()
    val connected by settingsVm.connected.collectAsState()

    var editHost by remember(host) { mutableStateOf(host) }
    var editPort by remember(port) { mutableStateOf(port.toString()) }
    var editToken by remember(token) { mutableStateOf(token) }

    Column(modifier = Modifier.fillMaxSize()) {
        ConnectionBar(connected = connected)

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Settings", style = MaterialTheme.typography.headlineLarge)

            OutlinedTextField(
                value = editHost,
                onValueChange = { editHost = it },
                label = { Text("Host IP") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )

            OutlinedTextField(
                value = editPort,
                onValueChange = { editPort = it },
                label = { Text("Port") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            )

            OutlinedTextField(
                value = editToken,
                onValueChange = { editToken = it },
                label = { Text("API Token (optional)") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )

            Spacer(Modifier.height(8.dp))

            Button(
                onClick = {
                    settingsVm.setHost(editHost.trim())
                    editPort.trim().toIntOrNull()?.let { settingsVm.setPort(it) }
                    settingsVm.setToken(editToken.trim())
                    settingsVm.checkConnection()
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Save & Test Connection")
            }

            Text(
                text = if (connected) "Connected" else "Not connected",
                color = if (connected) StatusGreen else StatusRed,
                style = MaterialTheme.typography.bodyLarge,
            )
        }
    }
}
