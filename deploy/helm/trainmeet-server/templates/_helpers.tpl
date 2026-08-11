{{- define "trainmeet-server.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "trainmeet-server.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "trainmeet-server.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "trainmeet-server.labels" -}}
app.kubernetes.io/name: {{ include "trainmeet-server.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "trainmeet-server.selectorLabels" -}}
app.kubernetes.io/name: {{ include "trainmeet-server.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

