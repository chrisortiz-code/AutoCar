package com.autocar.app.data.settings

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "settings")

class SettingsStore(private val context: Context) {

    companion object {
        private val HOST = stringPreferencesKey("host")
        private val PORT = intPreferencesKey("port")
        private val TOKEN = stringPreferencesKey("token")

        const val DEFAULT_HOST = "192.168.2.83"
        const val DEFAULT_PORT = 8080
    }

    val host: Flow<String> = context.dataStore.data.map { it[HOST] ?: DEFAULT_HOST }
    val port: Flow<Int> = context.dataStore.data.map { it[PORT] ?: DEFAULT_PORT }
    val token: Flow<String> = context.dataStore.data.map { it[TOKEN] ?: "" }

    val baseUrl: Flow<String> = context.dataStore.data.map { prefs ->
        val h = prefs[HOST] ?: DEFAULT_HOST
        val p = prefs[PORT] ?: DEFAULT_PORT
        "http://$h:$p/"
    }

    suspend fun setHost(value: String) {
        context.dataStore.edit { it[HOST] = value }
    }

    suspend fun setPort(value: Int) {
        context.dataStore.edit { it[PORT] = value }
    }

    suspend fun setToken(value: String) {
        context.dataStore.edit { it[TOKEN] = value }
    }
}
