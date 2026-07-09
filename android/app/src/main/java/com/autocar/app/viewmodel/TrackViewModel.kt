package com.autocar.app.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.autocar.app.data.api.ApiClient
import com.autocar.app.data.api.TrackApi
import com.autocar.app.data.model.SetRefBody
import com.autocar.app.data.model.TrackStartBody
import com.autocar.app.data.model.TrackState
import com.autocar.app.data.settings.SettingsStore
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class TrackViewModel(app: Application) : AndroidViewModel(app) {

    private val store = SettingsStore(app)

    private val _trackState = MutableStateFlow(TrackState())
    val trackState: StateFlow<TrackState> = _trackState.asStateFlow()

    private val _streamUrl = MutableStateFlow("")
    val streamUrl: StateFlow<String> = _streamUrl.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private var pollingJob: Job? = null

    private suspend fun api(): TrackApi {
        val baseUrl = store.baseUrl.first()
        val token = store.token.first()
        return ApiClient.get(baseUrl, token).create(TrackApi::class.java)
    }

    private fun refreshStatus() {
        viewModelScope.launch {
            try {
                _trackState.value = api().status()
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
                    _trackState.value = api().status()
                    _error.value = null
                } catch (e: Exception) {
                    _error.value = e.message
                }
                delay(500)
            }
        }
    }

    private fun stopPolling() {
        pollingJob?.cancel()
        pollingJob = null
    }

    fun start(backend: String, follow: Boolean) = viewModelScope.launch {
        try {
            val baseUrl = store.baseUrl.first()
            _streamUrl.value = baseUrl.trimEnd('/') + "/api/track/stream"
            api().start(TrackStartBody(backend = backend, follow = follow))
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
            _trackState.value = TrackState()
            _streamUrl.value = ""
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    fun setReference(x1: Float, y1: Float, x2: Float, y2: Float) = viewModelScope.launch {
        try {
            api().setRef(SetRefBody(x1, y1, x2, y2))
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

    /** Called when user navigates away from the Track tab. */
    fun onTabHidden() {
        stopPolling()
    }

    override fun onCleared() {
        stopPolling()
        super.onCleared()
    }
}
