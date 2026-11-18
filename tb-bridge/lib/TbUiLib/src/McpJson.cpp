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

#include "ui/McpJson.h"

#include <QJsonArray>

namespace tb::ui::mcp
{

namespace
{
double component(const QJsonValue& value, const char* key, double fallback)
{
  if (!value.isObject())
  {
    return fallback;
  }
  const auto member = value.toObject().value(QLatin1String{key});
  return member.isDouble() ? member.toDouble() : fallback;
}
} // namespace

vm::vec3d vec3FromJson(const QJsonValue& value, const vm::vec3d& fallback)
{
  if (value.isArray())
  {
    const auto array = value.toArray();
    if (array.size() >= 3)
    {
      return vm::vec3d{
        array.at(0).toDouble(fallback.x()),
        array.at(1).toDouble(fallback.y()),
        array.at(2).toDouble(fallback.z())};
    }
  }
  else if (value.isObject())
  {
    return vm::vec3d{
      component(value, "x", fallback.x()),
      component(value, "y", fallback.y()),
      component(value, "z", fallback.z())};
  }
  return fallback;
}

QJsonArray vec3ToJson(const vm::vec3d& value)
{
  return QJsonArray{value.x(), value.y(), value.z()};
}

vm::bbox3d bboxFromJson(const QJsonValue& value, const vm::bbox3d& fallback)
{
  if (!value.isObject())
  {
    return fallback;
  }

  const auto object = value.toObject();
  auto minValue = object.value(QLatin1String{"min"});
  if (minValue.isUndefined())
  {
    minValue = object.value(QLatin1String{"mins"});
  }
  auto maxValue = object.value(QLatin1String{"max"});
  if (maxValue.isUndefined())
  {
    maxValue = object.value(QLatin1String{"maxs"});
  }

  return vm::bbox3d{
    vec3FromJson(minValue, fallback.min), vec3FromJson(maxValue, fallback.max)};
}

QJsonObject bboxToJson(const vm::bbox3d& bounds)
{
  auto result = QJsonObject{};
  result.insert(QLatin1String{"min"}, vec3ToJson(bounds.min));
  result.insert(QLatin1String{"max"}, vec3ToJson(bounds.max));
  result.insert(QLatin1String{"size"}, vec3ToJson(bounds.size()));
  return result;
}

std::vector<std::string> stringArrayFromJson(const QJsonValue& value)
{
  auto result = std::vector<std::string>{};
  if (!value.isArray())
  {
    return result;
  }
  const auto array = value.toArray();
  result.reserve(static_cast<size_t>(array.size()));
  for (const auto& entry : array)
  {
    if (entry.isString())
    {
      result.push_back(entry.toString().toStdString());
    }
  }
  return result;
}

std::string stringFromJson(const QJsonValue& value, const std::string& fallback)
{
  return value.isString() ? value.toString().toStdString() : fallback;
}

double numberFromJson(const QJsonValue& value, double fallback)
{
  return value.isDouble() ? value.toDouble() : fallback;
}

bool boolFromJson(const QJsonValue& value, bool fallback)
{
  return value.isBool() ? value.toBool() : fallback;
}

QJsonObject makeError(int code, const std::string& message)
{
  auto error = QJsonObject{};
  error.insert(QLatin1String{"code"}, code);
  error.insert(QLatin1String{"message"}, QString::fromStdString(message));
  return error;
}

} // namespace tb::ui::mcp
