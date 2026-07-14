package com.autocar.app.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.autocar.app.data.api.ApiClient
import com.autocar.app.data.api.ColorApi
import com.autocar.app.data.model.ColorClickBody
import com.autocar.app.data.model.ColorStartBody
import com.autocar.app.data.model.ColorState
import com.autocar.app.data.settings.SettingsStore
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class ColorViewModel(app: Application) : AndroidViewModel(app) {

    private val store = SettingsStore(app)

    private val _colorState = MutableStateFlow(ColorState())
    val colorState: StateFlow<ColorState> = _colorState.asStateFlow()

    private val _streamUrl = MutableStateFlow("")
    val streamUrl: StateFlow<String> = _streamUrl.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private var pollingJob: Job? = null

    private suspend fun api(): ColorApi {
        val baseUrl = store.baseUrl.first()
        val token = store.token.first()
        return ApiClient.get(baseUrl, token).create(ColorApi::class.java)
    }

    private fun refreshStatus() {
        viewModelScope.launch {
            try {
                _colorState.value = api().status()
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
                    _colorState.value = api().status()
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

    fun start(follow: Boolean) = viewModelScope.launch {
        try {
            val baseUrl = store.baseUrl.first()
            _streamUrl.value = baseUrl.trimEnd('/') + "/api/color/stream"
            api().start(ColorStartBody(follow = follow))
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
            _colorState.value = ColorState()
            _streamUrl.value = ""
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    fun clickColor(x: Float, y: Float) = viewModelScope.launch {
        try {
            api().click(ColorClickBody(x, y))
            refreshStatus()
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    fun confirm() = viewModelScope.launch {
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

    fun onTabHidden() {
        stopPolling()
    }

    override fun onCleared() {
        stopPolling()
        super.onCleared()
    }
}
