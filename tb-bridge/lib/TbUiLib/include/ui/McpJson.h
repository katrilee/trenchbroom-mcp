/*
 Copyright (C) 2026 TrenchBroom MCP Bridge contributors

 This file is part of TrenchBroom.

 TrenchBroom is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.

 TrenchBroom is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with TrenchBroom. If not, see <http://www.gnu.org/licenses/>.
 */

#pragma once

#include "vm/bbox.h"
#include "vm/vec.h"

#include <QJsonObject>
#include <QJsonValue>

#include <string>
#include <vector>

namespace tb::ui::mcp
{

/**
 * Conversions between QJsonValue and TrenchBroom's math types.
 *
 * Vectors accept the compact `[x, y, z]` form or the verbose `{"x":..,"y":..,
 * "z":..}` form, so an LLM client can use whichever reads better.
 */

vm::vec3d vec3FromJson(const QJsonValue& value, const vm::vec3d& fallback = {0, 0, 0});
QJsonArray vec3ToJson(const vm::vec3d& value);

vm::bbox3d bboxFromJson(
  const QJsonValue& value,
  const vm::bbox3d& fallback = {{-64, -64, -64}, {64, 64, 64}});
QJsonObject bboxToJson(const vm::bbox3d& bounds);

std::vector<std::string> stringArrayFromJson(const QJsonValue& value);

std::string stringFromJson(const QJsonValue& value, const std::string& fallback = "");
double numberFromJson(const QJsonValue& value, double fallback = 0.0);
bool boolFromJson(const QJsonValue& value, bool fallback = false);

namespace ErrorCode
{
constexpr int ParseError = -32700;
constexpr int InvalidRequest = -32600;
constexpr int MethodNotFound = -32601;
constexpr int InvalidParams = -32602;
constexpr int InternalError = -32603;

constexpr int NoDocument = -32001;
constexpr int NoSelection = -32002;
constexpr int OperationFailed = -32003;
constexpr int BadState = -32004;
} // namespace ErrorCode

QJsonObject makeError(int code, const std::string& message);

} // namespace tb::ui::mcp
