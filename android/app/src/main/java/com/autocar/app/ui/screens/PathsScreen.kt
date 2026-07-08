package com.autocar.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.autocar.app.ui.theme.Gold
import com.autocar.app.viewmodel.PathsViewModel

@Composable
fun PathsScreen(pathsVm: PathsViewModel = viewModel()) {
    val paths by pathsVm.paths.collectAsState()
    val status by pathsVm.status.collectAsState()
    val error by pathsVm.error.collectAsState()
    var showCreate by remember { mutableStateOf(false) }
    var newName by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        pathsVm.loadPaths()
        pathsVm.loadStatus()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("Paths", style = MaterialTheme.typography.headlineLarge, color = Gold)

        Button(
            onClick = { showCreate = true },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Icon(Icons.Default.Add, contentDescription = null, modifier = Modifier.padding(end = 4.dp))
            Text("New Path")
        }

        if (status.status != "idle") {
            Card(
                modifier = Modifier.fillMaxWidth(),
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column {
                        Text("Status: ${status.status}", style = MaterialTheme.typography.titleLarge)
                        status.step?.let { step ->
                            Text("Step ${step}/${status.total_steps ?: "?"}")
                        }
                    }
                    status.path_id?.let { pid ->
                        IconButton(onClick = { pathsVm.stopExecution(pid) }) {
                            Icon(Icons.Default.Stop, contentDescription = "Stop")
                        }
                    }
                }
            }
        }

        error?.let {
            Text(text = it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(vertical = 4.dp))
        }

        paths.forEach { path ->
            Card(modifier = Modifier.fillMaxWidth()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(path.name, style = MaterialTheme.typography.titleLarge)
                        Text("${path.path_type} - ${path.steps} steps", style = MaterialTheme.typography.bodyLarge)
                    }
                    Row {
                        IconButton(onClick = { pathsVm.executePath(path.id) }) {
                            Icon(Icons.Default.PlayArrow, contentDescription = "Execute")
                        }
                        IconButton(onClick = { pathsVm.deletePath(path.id) }) {
                            Icon(Icons.Default.Delete, contentDescription = "Delete")
                        }
                    }
                }
            }
        }
    }

    if (showCreate) {
        AlertDialog(
            onDismissRequest = { showCreate = false },
            title = { Text("New Path") },
            text = {
                OutlinedTextField(
                    value = newName,
                    onValueChange = { newName = it },
                    label = { Text("Path name") },
                    singleLine = true,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    pathsVm.createPath(newName.ifBlank { "Untitled" })
                    newName = ""
                    showCreate = false
                }) {
                    Text("Create")
                }
            },
            dismissButton = {
                TextButton(onClick = { showCreate = false }) {
                    Text("Cancel")
                }
            },
        )
    }
}
