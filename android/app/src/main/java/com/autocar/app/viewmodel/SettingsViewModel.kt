package com.autocar.app.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.autocar.app.data.settings.SettingsStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class SettingsViewModel(app: Application) : AndroidViewModel(app) {

    val store = SettingsStore(app)

    val host: StateFlow<String> = store.host
        .stateIn(viewModelScope, SharingStarted.Eagerly, SettingsStore.DEFAULT_HOST)

    val port: StateFlow<Int> = store.port
        .stateIn(viewModelScope, SharingStarted.Eagerly, SettingsStore.DEFAULT_PORT)

    val token: StateFlow<String> = store.token
        .stateIn(viewModelScope, SharingStarted.Eagerly, "")

    val baseUrl: StateFlow<String> = store.baseUrl
        .stateIn(viewModelScope, SharingStarted.Eagerly, "http://${SettingsStore.DEFAULT_HOST}:${SettingsStore.DEFAULT_PORT}/")

    private val _connected = MutableStateFlow(false)
    val connected: StateFlow<Boolean> = _connected.asStateFlow()

    fun setHost(value: String) = viewModelScope.launch { store.setHost(value) }
    fun setPort(value: Int) = viewModelScope.launch { store.setPort(value) }
    fun setToken(value: String) = viewModelScope.launch { store.setToken(value) }

    fun checkConnection() = viewModelScope.launch {
        try {
            val url = baseUrl.value
            val client = okhttp3.OkHttpClient.Builder()
                .connectTimeout(3, java.util.concurrent.TimeUnit.SECONDS)
                .build()
            val request = okhttp3.Request.Builder().url(url).build()
            val response = client.newCall(request).execute()
            _connected.value = response.isSuccessful
            response.close()
        } catch (_: Exception) {
            _connected.value = false
        }
    }
}
