package com.autocar.app.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.autocar.app.data.api.ApiClient
import com.autocar.app.data.api.FaceApi
import com.autocar.app.data.model.ClickBody
import com.autocar.app.data.model.FaceState
import com.autocar.app.data.model.StartBody
import com.autocar.app.data.settings.SettingsStore
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class FaceViewModel(app: Application) : AndroidViewModel(app) {

    private val store = SettingsStore(app)

    private val _faceState = MutableStateFlow(FaceState())
    val faceState: StateFlow<FaceState> = _faceState.asStateFlow()

    private val _streamUrl = MutableStateFlow("")
    val streamUrl: StateFlow<String> = _streamUrl.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private var pollingJob: Job? = null

    private suspend fun api(): FaceApi {
        val baseUrl = store.baseUrl.first()
        val token = store.token.first()
        return ApiClient.get(baseUrl, token).create(FaceApi::class.java)
    }

    private fun refreshStatus() {
        viewModelScope.launch {
            try {
                _faceState.value = api().status()
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
                    _faceState.value = api().status()
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
            _streamUrl.value = baseUrl.trimEnd('/') + "/api/face/stream"
            api().start(StartBody(backend = backend, follow = follow))
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
            _faceState.value = FaceState()
            _streamUrl.value = ""
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    fun clickFace(x: Float, y: Float) = viewModelScope.launch {
        try {
            api().click(ClickBody(x, y))
            refreshStatus()
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    fun confirmFace() = viewModelScope.launch {
        try {
            api().confirm()
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

    override fun onCleared() {
        stopPolling()
        super.onCleared()
    }
}
