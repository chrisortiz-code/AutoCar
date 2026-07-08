package com.autocar.app.data.api

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import java.util.concurrent.TimeUnit

object ApiClient {
    private val json = Json { ignoreUnknownKeys = true }

    private var retrofit: Retrofit? = null
    private var currentBaseUrl: String = ""
    private var currentToken: String = ""

    fun get(baseUrl: String, token: String = ""): Retrofit {
        if (retrofit != null && baseUrl == currentBaseUrl && token == currentToken) {
            return retrofit!!
        }

        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BODY
        }

        val client = OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor { token })
            .addInterceptor(logging)
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(10, TimeUnit.SECONDS)
            .writeTimeout(10, TimeUnit.SECONDS)
            .build()

        val contentType = "application/json".toMediaType()

        retrofit = Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(json.asConverterFactory(contentType))
            .build()

        currentBaseUrl = baseUrl
        currentToken = token
        return retrofit!!
    }

    fun okHttpClient(token: String = ""): OkHttpClient {
        return OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor { token })
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.SECONDS) // no timeout for streams/ws
            .build()
    }
}
