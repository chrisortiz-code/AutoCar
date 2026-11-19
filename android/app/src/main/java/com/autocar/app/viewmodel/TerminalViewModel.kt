package com.autocar.app.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.autocar.app.data.settings.SettingsStore
import com.autocar.app.data.ssh.SshManager
import com.autocar.app.data.ssh.SshProcess
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

enum class ProcessId { API_SERVER, UNIVERSAL_RECEIVER }

enum class ProcessState { STOPPED, STARTING, RUNNING, STOPPING, ERROR }

data class ProcessStatus(
    val state: ProcessState = ProcessState.STOPPED,
    val lines: List<String> = emptyList(),
    val error: String? = null,
)

data class TerminalUiState(
    val sshConnected: Boolean = false,
    val connecting: Boolean = false,
    val connectionError: String? = null,
    val diffDrive: Boolean = false,
    val api: ProcessStatus = ProcessStatus(),
    val receiver: ProcessStatus = ProcessStatus(),
)

class TerminalViewModel(app: Application) : AndroidViewModel(app) {

    private val store = SettingsStore(app)
    private val sshManager = SshManager()

    private val _state = MutableStateFlow(TerminalUiState())
    val state: StateFlow<TerminalUiState> = _state.asStateFlow()

    private var apiProcess: SshProcess? = null
    private var receiverProcess: SshProcess? = null
    private var apiJob: Job? = null
    private var receiverJob: Job? = null

    companion object {
        private const val MAX_LINES = 500
        private const val WORK_DIR = "/home/jamesortiz17/AutoCar"
        private const val API_CMD = "cd $WORK_DIR && python -m api"
        private const val RECEIVER_CMD = "cd $WORK_DIR && python robot/universal_receiver.py"
    }

    fun setDiffDrive(enabled: Boolean) {
        _state.value = _state.value.copy(diffDrive = enabled)
    }

    fun connectSsh() = viewModelScope.launch(Dispatchers.IO) {
        _state.value = _state.value.copy(connecting = true, connectionError = null)
        try {
            val host = store.host.first()
            val username = store.sshUsername.first()
            val password = store.sshPassword.first()
            sshManager.connect(host, username, password)
            _state.value = _state.value.copy(sshConnected = true, connecting = false)
        } catch (e: Exception) {
            _state.value = _state.value.copy(
                sshConnected = false,
                connecting = false,
                connectionError = e.message ?: "SSH connection failed",
            )
        }
    }

    fun disconnectSsh() {
        stopProcess(ProcessId.API_SERVER)
        stopProcess(ProcessId.UNIVERSAL_RECEIVER)
        sshManager.disconnect()
        _state.value = _state.value.copy(sshConnected = false)
    }

    fun startProcess(id: ProcessId) {
        val diffFlag = if (_state.value.diffDrive) " --diff-drive" else ""
        val cmd = when (id) {
            ProcessId.API_SERVER -> API_CMD
            ProcessId.UNIVERSAL_RECEIVER -> RECEIVER_CMD + diffFlag
        }
        updateProcess(id) { it.copy(state = ProcessState.STARTING, lines = emptyList(), error = null) }

        val job = viewModelScope.launch(Dispatchers.IO) {
            try {
                val process = sshManager.execStreaming(cmd)
                when (id) {
                    ProcessId.API_SERVER -> apiProcess = process
                    ProcessId.UNIVERSAL_RECEIVER -> receiverProcess = process
                }
                updateProcess(id) { it.copy(state = ProcessState.RUNNING) }

                process.output.collect { line ->
                    updateProcess(id) { status ->
                        val newLines = (status.lines + line).takeLast(MAX_LINES)
                        status.copy(lines = newLines)
                    }
                }
                updateProcess(id) { it.copy(state = ProcessState.STOPPED) }
            } catch (e: Exception) {
                updateProcess(id) { it.copy(state = ProcessState.ERROR, error = e.message) }
            }
        }

        when (id) {
            ProcessId.API_SERVER -> apiJob = job
            ProcessId.UNIVERSAL_RECEIVER -> receiverJob = job
        }
    }

    fun stopProcess(id: ProcessId) {
        updateProcess(id) { it.copy(state = ProcessState.STOPPING) }
        viewModelScope.launch(Dispatchers.IO) {
            when (id) {
                ProcessId.API_SERVER -> {
                    apiProcess?.stop()
                    apiJob?.cancel()
                    apiProcess = null
                }
                ProcessId.UNIVERSAL_RECEIVER -> {
                    receiverProcess?.stop()
                    receiverJob?.cancel()
                    receiverProcess = null
                }
            }
            updateProcess(id) { it.copy(state = ProcessState.STOPPED) }
        }
    }

    fun clearLog(id: ProcessId) {
        updateProcess(id) { it.copy(lines = emptyList()) }
    }

    private fun updateProcess(id: ProcessId, update: (ProcessStatus) -> ProcessStatus) {
        _state.value = when (id) {
            ProcessId.API_SERVER -> _state.value.copy(api = update(_state.value.api))
            ProcessId.UNIVERSAL_RECEIVER -> _state.value.copy(receiver = update(_state.value.receiver))
        }
    }

    override fun onCleared() {
        apiProcess?.stop()
        receiverProcess?.stop()
        apiJob?.cancel()
        receiverJob?.cancel()
        sshManager.disconnect()
        super.onCleared()
    }
}
