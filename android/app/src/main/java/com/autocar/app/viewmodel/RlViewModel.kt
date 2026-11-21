package com.autocar.app.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.autocar.app.data.api.ApiClient
import com.autocar.app.data.api.RlApi
import com.autocar.app.data.model.RlSetRefBody
import com.autocar.app.data.model.RlStartBody
import com.autocar.app.data.model.RlState
import com.autocar.app.data.model.SafetyStatus
import com.autocar.app.data.settings.SettingsStore
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class RlViewModel(app: Application) : AndroidViewModel(app) {

    private val store = SettingsStore(app)

    private val _rlState = MutableStateFlow(RlState())
    val rlState: StateFlow<RlState> = _rlState.asStateFlow()

    private val _streamUrl = MutableStateFlow("")
    val streamUrl: StateFlow<String> = _streamUrl.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private val _checkpoints = MutableStateFlow<List<String>>(emptyList())
    val checkpoints: StateFlow<List<String>> = _checkpoints.asStateFlow()

    private val _safetyStatus = MutableStateFlow(SafetyStatus())
    val safetyStatus: StateFlow<SafetyStatus> = _safetyStatus.asStateFlow()

    private var pollingJob: Job? = null

    init {
        fetchCheckpoints()
        fetchSafetyStatus()
    }

    fun fetchCheckpoints() = viewModelScope.launch {
        try {
            _checkpoints.value = api().checkpoints().checkpoints
        } catch (_: Exception) {}
    }

    private suspend fun api(): RlApi {
        val baseUrl = store.baseUrl.first()
        val token = store.token.first()
        return ApiClient.get(baseUrl, token).create(RlApi::class.java)
    }

    private fun refreshStatus() {
        viewModelScope.launch {
            try {
                _rlState.value = api().status()
                _error.value = null
            } catch (e: Exception) {
                _error.value = e.message
            }
        }
    }

    private fun startPolling() {
        pollingJob?.cancel()
        pollingJob = viewModelScope.launch {
            while (true) {
                try {
                    _rlState.value = api().status()
                    _error.value = null
                } catch (e: Exception) {
                    _error.value = e.message
                }
                delay(250)
            }
        }
    }

    private fun stopPolling() {
        pollingJob?.cancel()
        pollingJob = null
    }

    fun start(desiredDist: Float = 1.0f, checkpoint: String = "", backend: String = "csrt") = viewModelScope.launch {
        try {
            val baseUrl = store.baseUrl.first()
            _streamUrl.value = baseUrl.trimEnd('/') + "/api/rl/stream"
            val ckptPath = if (checkpoint.isNotEmpty())
                "rl_policy/checkpoints/$checkpoint" else ""
            api().start(RlStartBody(desiredDist = desiredDist, checkpoint = ckptPath, backend = backend))
            _error.value = null
            startPolling()
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    fun stop() = viewModelScope.launch {
        try {
            api().stop()
            _error.value = null
            stopPolling()
            _rlState.value = RlState()
            _streamUrl.value = ""
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    fun setReference(x1: Float, y1: Float, x2: Float, y2: Float) = viewModelScope.launch {
        try {
            api().setRef(RlSetRefBody(x1, y1, x2, y2))
            refreshStatus()
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    fun reset() = viewModelScope.launch {
        try {
            api().reset()
            refreshStatus()
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    private fun fetchSafetyStatus() = viewModelScope.launch {
        try {
            _safetyStatus.value = api().safetyStatus()
        } catch (_: Exception) {}
    }

    fun toggleSafety() = viewModelScope.launch {
        try {
            if (_safetyStatus.value.enabled) {
                api().safetyDisable()
            } else {
                api().safetyEnable()
            }
            _safetyStatus.value = api().safetyStatus()
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    fun onTabHidden() {
        stopPolling()
    }

    override fun onCleared() {
        stopPolling()
        super.onCleared()
    }
}
