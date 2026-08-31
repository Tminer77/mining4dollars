#pragma once

#include "CoreMinimal.h"

/** Original coastal grid. Units are Unreal centimetres. Not a Rockstar map. */
namespace AureliaWorld
{
	inline constexpr int32 Grid = 6;
	inline constexpr float Block = 5600.f;
	inline constexpr float Road = 1800.f;
	inline constexpr float Cell = Block + Road;
	inline constexpr float Ring = 14800.f;
	inline constexpr int32 LapCount = 3;
	inline constexpr int32 GateCount = 8;
}
