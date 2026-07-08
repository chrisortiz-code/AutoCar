package com.autocar.app.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.autocar.app.data.api.ApiClient
import com.autocar.app.data.api.PathsApi
import com.autocar.app.data.model.CreatePath
import com.autocar.app.data.model.PathStatus
import com.autocar.app.data.model.PathSummary
import com.autocar.app.data.settings.SettingsStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class PathsViewModel(app: Application) : AndroidViewModel(app) {

    private val store = SettingsStore(app)

    private val _paths = MutableStateFlow<List<PathSummary>>(emptyList())
    val paths: StateFlow<List<PathSummary>> = _paths.asStateFlow()

    private val _status = MutableStateFlow(PathStatus())
    val status: StateFlow<PathStatus> = _status.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private suspend fun api(): PathsApi {
        val baseUrl = store.baseUrl.first()
        val token = store.token.first()
        return ApiClient.get(baseUrl, token).create(PathsApi::class.java)
    }

    fun loadPaths() = viewModelScope.launch {
        try {
            _paths.value = api().list()
            _error.value = null
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    fun loadStatus() = viewModelScope.launch {
        try {
            _status.value = api().status()
        } catch (_: Exception) {}
    }

    fun createPath(name: String, type: String = "sequential") = viewModelScope.launch {
        try {
            api().create(CreatePath(name, type))
            loadPaths()
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    fun deletePath(id: Int) = viewModelScope.launch {
        try {
            api().delete(id)
            loadPaths()
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    fun executePath(id: Int) = viewModelScope.launch {
        try {
            api().execute(id)
            _error.value = null
        } catch (e: Exception) {
            _error.value = e.message
        }
    }

    fun stopExecution(id: Int) = viewModelScope.launch {
        try {
            api().stopExecution(id)
        } catch (e: Exception) {
            _error.value = e.message
        }
    }
}
